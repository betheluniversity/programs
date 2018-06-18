import ast
import copy
import datetime
from datetime import datetime as DT, timedelta
import json
import locale
import requests
import time
import unicodedata
import xml.etree.ElementTree as ET

from banner import Banner
from bu_cascade.assets.block import Block
from bu_cascade.cascade_connector import Cascade
from config import WSDL, AUTH, SITE_ID, STAGING_DESTINATION_ID, XML_URL, PUBLISHSET_ID, MISSING_DATA_MESSAGE
from descriptions import delivery_descriptions, locations, labels, subheadings
from flask import Flask, render_template, Response, stream_with_context
from flask.ext.classy import FlaskView, route
from mail import send_message
from sqlalchemy.engine.result import RowProxy

from manual_cost_per_credits import MANUAL_COST_PER_CREDITS, MISSING_CODES
from program_codes_to_skip import SKIP_CODES


app = Flask(__name__)
app.config.from_object('config')

from raven.contrib.flask import Sentry
sentry = Sentry(app, dsn=app.config['RAVEN_URL'])


class CascadeBlockProcessor:
    def __init__(self):
        self.banner = Banner()
        self.cascade = Cascade(WSDL, AUTH, SITE_ID, STAGING_DESTINATION_ID)
        self.hashes = set()
        # todo: better names
        self.missing = []
        self.missing_locations = []
        self.new_hashes = set()
        # self.data = [row for row in self.banner.get_program_data()]

    def process_all_blocks(self, time_to_wait, send_email_after):
        # It should be noted that this only streams to Chrome; Firefox tries to download the JS as a file.

        def generator():
            newline = "<br/>"
            yield "Beginning sync of all blocks" + newline*2
            r = requests.get(XML_URL)
            # Process the r.text to find the errant, non-ASCII characters
            safe_text = unicodedata.normalize('NFKD', r.text).encode('ascii', 'ignore')
            block_xml = ET.fromstring(safe_text)
            blocks = []

            # Any blocks that begin with any of these paths will NOT by synced by this method
            paths_to_ignore = ["_shared-content/program-blocks/undergrad", "_shared-content/program-blocks/seminary"]

            for e in block_xml.findall('.//system-block'):
                block_id = e.get('id')
                block_path = e.find('path').text
                if any([path in block_path for path in paths_to_ignore]):
                    continue
                # block = Block(self.cascade, block_id)
                # read_asset returns a string version of a python dict. todo, move this to connector
                result = self.process_block(block_id)
                blocks.append(result)
                yield result + newline
                time.sleep(time_to_wait)
            yield newline + "All blocks have been synced."
            if send_email_after:
                # compare hashes to SQL
                self.check_hashes()

                caps_gs = [MISSING_DATA_MESSAGE + "<br/>"]
                if len(self.missing):
                    for code in self.missing:
                        # will there be a 2- for some reason outside of a code?
                        if '2-' in code:
                            caps_gs.append(code)

                caps_gs.append("<br/>If you have any questions, please email web-services@bethel.edu.")
                if len(caps_gs) > 2:  # Only send an email to the CAPS/GS contacts if there's an errant block
                    send_message("No CAPS/GS Banner Data Found", "<br/>".join(caps_gs), html=True, caps_gs=True)
                self.cascade.publish(PUBLISHSET_ID, 'publishset')
                self.create_readers_digest()

        return Response(stream_with_context(generator()))  # , mimetype='text/json')

    def process_block_by_path(self, path):
        block_id = ast.literal_eval(Block(self.cascade, "/"+path).asset)['xhtmlDataDefinitionBlock']['id']
        return self.process_block_by_id(block_id)

    def process_block_by_id(self, id):
        result = self.process_block(id)
        # AnnMarie decided that the block should be synced, but not published. Either it will be published with the next
        # big set, or they can publish it manually. As such, the line below should remain commented out.
        # self.cascade.publish(PUBLISHSET_ID, 'publishset')
        return result

    def find(self, search_list, key):
        for item in search_list:
            if item['identifier'] == key:
                return item

    def find_all(self, search_list, key):
        matches = []
        for item in search_list:
            if item['identifier'] == key:
                matches.append(item)

        return matches

    def create_readers_digest(self):
        '''
        Send a 'readers digest' email with info about the sync to alert everyone about errors.
        The email has 3 sections:
            1. Codes that are in blocks but returned no data
            2. Codes we got data for but are not in any blocks
            3. Location codes we got in data but we don't have a mapping for
        :return: None
        :rtype: None
        '''

        missing = self.missing
        missing_locations = self.missing_locations
        new_hashes = self.new_hashes

        email_body = render_template("readers_digest.html", **locals())
        send_message("Readers Digest: Program Sync", email_body, html=True)

    def get_data_for_code(self, code):
        self_data = [row for row in self.banner.get_program_data()]
        results = []
        # for row in self.data:
        for row in self_data:
            if row['program_code'] == code:
                results.append(row)
        return results

    def check_hashes(self):
        # data = self.data
        data = [row for row in self.banner.get_program_data()]
        banner_hashes = []
        row_data = {}
        for row in data:
            row_hash = row.values()[0]
            more_row_data = self.get_data_for_code(row_hash)
            if more_row_data:
                for data_entry in more_row_data:
                    row_data[row_hash] = [str(val) for val in data_entry.values()]
                banner_hashes.append(row_hash)

        banner_hashes = set(banner_hashes)

        self.new_hashes = banner_hashes.difference(self.hashes)

    def process_block(self, block_id):
        program_block = Block(self.cascade, block_id)
        block_data = program_block.asset
        if isinstance(block_data, tuple):
            block_data = block_data[0]
        # Dates don't edit well
        my_path = block_data['xhtmlDataDefinitionBlock']['path']
        for key in block_data['xhtmlDataDefinitionBlock'].keys():
            if key.endswith('Date'):
                del block_data['xhtmlDataDefinitionBlock'][key]

        block_properties = block_data['xhtmlDataDefinitionBlock']

        if block_properties['structuredData']['definitionPath'] not in ["Blocks/Program", "Test/Phil's Block"]:
            return my_path + " not in Blocks/Program"
        if 'seminary' in block_properties['path']:
            return my_path + " has 'seminary' in its path"

        nodes = block_properties['structuredData']['structuredDataNodes']['structuredDataNode']

        # mark the code down as "seen"
        try:
            program_hash = nodes[0]['structuredDataNodes']['structuredDataNode'][0]['text']
            self.hashes.add(program_hash)
        except KeyError:
            # not all programs have generic codes -- only concentration codes.
            pass

        # every node after the first is a concentration
        for concentration_structure in nodes[1:]:

            concentration = concentration_structure['structuredDataNodes']['structuredDataNode']

            try:
                concentration_code = concentration[0]['text']

                # load the data from banner for this code
                data = self.get_data_for_code(concentration_code)
                if len(data) > 0:
                    concentration_code_has_data = True
                else:
                    concentration_code_has_data = False

                # If the concentration code ('2-MA-COUG' for example) is in the list of programs to skip,
                # continue iterating over the concentrations in this block
                if concentration_code in SKIP_CODES:
                    print "Code '%s' found in skip list; skipping it" % concentration_code
                    if concentration_code_has_data:
                        print "Although code '%s' is being skipped, it has data in Banner." % concentration_code
                    print ""

                    continue
            except KeyError:
                continue

            # some have courses entered so the index isn't the same. use the last one
            banner_info = concentration[len(concentration) - 1]['structuredDataNodes']['structuredDataNode']

            if not data:
                print "No data found for program code %s, even though it's supposed to sync" % concentration_code
                # If data ever does not exist, then this used to clear out the concentration name and cost per credit.
                # This caused issues because cost per credit has been in limbo and we want to manually set it.
                # self.find(banner_info, 'concentration_name')['text'] = ""
                # self.find(banner_info, 'cost')['text'] = ""
                details = self.find(banner_info, 'cohort_details')['structuredDataNodes']['structuredDataNode']
                for item in details:
                    item['text'] = ""

            # update block
            cohort_details = self.find_all(banner_info, 'cohort_details')
            # down to 1 delivery detail, in case any got removed. Just re-populate them all
            if len(cohort_details) > 1:
                for entry in cohort_details[1:]:
                    banner_info.remove(entry)
                cohort_details = self.find_all(banner_info, 'cohort_details')

            data_copy_list = []
            for row in data:
                if isinstance(row, RowProxy):
                    row_dict = {}
                    for key in row.keys():
                        row_dict[key] = row[key]

                    if ';' in row_dict['start_date']:
                        formats = ["%m/%d/%Y", "%mm/%d/%Y", "%m/%dd/%Y", "%mm/%dd/%Y"]
                        for start_date in row_dict['start_date'].split(';'):
                            calendar = None
                            for fmt in formats:
                                try:
                                    calendar = DT.strptime(start_date.strip(), fmt)
                                except ValueError:
                                    continue
                            if calendar is None:
                                continue
                            start_date = start_date.strip() + ';'
                            two_weeks_from_now = datetime.datetime.now() + timedelta(days=14)
                            if calendar > two_weeks_from_now:
                                local_copy = copy.deepcopy(row_dict)
                                local_copy['start_date'] = start_date
                                data_copy_list.append(local_copy)
                    else:
                        data_copy_list.append(row_dict)

            j = None
            for j, row in enumerate(data_copy_list):
                # concentration
                self.find(banner_info, 'concentration_name')['text'] = row['concentration_name']

                # if row['cost_per_credit']:
                #     self.find(banner_info, 'cost')['text'] = "$" + str(row['cost_per_credit'])
                # else:
                #     print "Row missing cost per credit:", row
                #     print "Attempting to get manual price per credit"
                #     if row['program_code'] in MANUAL_COST_PER_CREDITS:
                #         print "Code found in MANUAL_COST_PER_CREDITS; using that."
                #         self.find(banner_info, 'cost')['text'] = MANUAL_COST_PER_CREDITS[row['program_code']]
                #     elif row['program_code'] in MISSING_CODES:
                #         print "Code found in MISSING_CODES, so it's ok if it isn't synced"
                #     else:
                #         print "Code not found in either manual list; THIS IS A REALLY BIG PROBLEM!"
                #     print ""

                # add a new detail for each row in the SQL result set.
                if len(cohort_details) <= j:
                    # Its going to be immediately overwritten by the new SQL row so it doesn't matter which node
                    banner_info.append(copy.deepcopy(cohort_details[0]))
                    # re-populate the list with the new item added so we can select it
                    cohort_details = self.find_all(banner_info, 'cohort_details')

                details = cohort_details[j]['structuredDataNodes']['structuredDataNode']

                delivery_code = row['delivery_code']
                delivery_label = row['delivery_label']
                if not delivery_label:
                    try:
                        delivery_label = labels[delivery_code]
                    except KeyError:
                        delivery_label = ""
                try:
                    delivery_row_code = delivery_descriptions[row['delivery_code']]
                except KeyError:
                    delivery_row_code = ""

                self.find(details, 'delivery_description')['text'] = delivery_row_code
                self.find(details, 'delivery_label')['text'] = delivery_label

                # adding delivery sub headings
                try:
                    delivery_subheadings = subheadings[delivery_code]
                except KeyError:
                    delivery_subheadings = ""
                if self.find(details, 'delivery_subheading'):
                    self.find(details, 'delivery_subheading')['text'] = delivery_subheadings
                else:
                    details.append({'text': delivery_subheadings, 'identifier': 'delivery_subheading', 'type': 'text'})

                try:
                    location = locations[row['location']]
                except KeyError:
                    location = ""
                    self.missing_locations.append(row['location'])

                if delivery_code in ['O', 'OO']:
                    location = 'Online'

                self.find(details, 'location')['text'] = location

                if ';' in row['start_date']:
                    # turn '4/17/2017;  05/29/2017; 07/10/2017' into usefulness
                    calendar = datetime.datetime.strptime(row['start_date'], "%m/%d/%Y;")
                    calendar = calendar.strftime("%m-%d-%Y")
                    self.find(details, 'cohort_start_type')['text'] = "Calendar"
                    self.find(details, 'calendar_start')['text'] = calendar
                    self.find(details, 'semester_start')['text'] = ""
                    self.find(details, 'year_start')['text'] = ""
                else:
                    # break up 'Fall 2015 - CAPS/GS' to 'Fall' and '2015'
                    term, year = row['start_date'].split(' - ')[0].split(' ')
                    self.find(details, 'cohort_start_type')['text'] = "Semester"
                    self.find(details, 'semester_start')['text'] = term
                    self.find(details, 'year_start')['text'] = year

                # def format_price_range(int_low, int_high):
                #     locale.setlocale(locale.LC_ALL, 'en_US')
                #     low = locale.format("%d", int_low, grouping=True)
                #     high = locale.format("%d", int_high, grouping=True)
                #     if int_low == int_high:
                #         return "$" + low
                #     else:
                #         return "$" + low + " - " + high
                #
                # # Derek said that if there's a min cost, there will also be a max cost. If they're different, then make
                # # it a range. If they're the same, then it's a fixed cost.
                # if row.get("min_cred_cost") and row.get("max_cred_cost"):
                #     min_credit = row.get("min_cred_cost")
                #     max_credit = row.get("max_cred_cost")
                #     self.find(banner_info, 'cost')['text'] = format_price_range(min_credit, max_credit)
                #
                # if row.get("min_prog_cost") and row.get("max_prog_cost"):
                #     min_program = row.get("min_prog_cost")
                #     max_program = row.get("max_prog_cost")
                #     self.find(banner_info, 'concentration_cost')['text'] = format_price_range(min_program, max_program)

            # consider 0 a good value as the first row in enumerate has j=0
            if j is None:
                self.missing.append(
                    """ %s (%s) """ % (block_properties['name'], concentration_code)
                )
            else:
                # mark the code down as "seen"
                self.hashes.add(concentration_code)

        asset = {
            'xhtmlDataDefinitionBlock': block_data['xhtmlDataDefinitionBlock']
        }
        try:
            program_block.edit_asset(asset)
        except:
            sentry.captureException()
            return my_path + " failed to sync"

        return my_path + " successfully updated and synced"


class AdultProgramsView(FlaskView):
    def __init__(self):
        self.send_email = False
        self.cbp = CascadeBlockProcessor()

    def index(self):
        return render_template("sync_template.html")

    @route("/sync-all/<time_interval>")
    @route("/sync-all/<time_interval>/<send_email>")
    def sync_all(self, time_interval, send_email=False):
        time_interval = float(time_interval)
        if send_email:
            self.send_email = True
        return self.cbp.process_all_blocks(time_interval, self.send_email)

    @route("/sync-one-id/<identifier>")
    def sync_one_id(self, identifier):
        return self.cbp.process_block_by_id(identifier)

    @route("/sync-one-path/<path:path>")
    def sync_one_path(self, path):
        return self.cbp.process_block_by_path(path)


AdultProgramsView.register(app)


if __name__ == "__main__":
    app.run(debug=True)
