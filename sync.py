import ast
import copy
import json
import requests
import time
import unicodedata
import xml.etree.ElementTree as ET

from banner import Banner
from bu_cascade.assets.block import Block
from bu_cascade.cascade_connector import Cascade
from config import WSDL, AUTH, SITE_ID, XML_URL, PUBLISHSET_ID, MISSING_DATA_MESSAGE
from descriptions import delivery_descriptions, locations, labels, subheadings
from flask import Flask, render_template, Response, session
from flask.json import JSONEncoder
from flask.ext.classy import FlaskView, route
from mail import send_message


class MyJSONEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, CascadeBlockProcessor):
            return {
                # TODO: it would be nice if this serialization could actually carry over, rather than just being
                # a stand-in to make the serialization error go away

                # 'banner': obj.banner,
                # 'cascade': obj.cascade,
                'hashes': obj.hashes,
                'missing': obj.missing,
                'missing_locations': obj.missing_locations,
                'new_hashes': obj.new_hashes,
                'data': obj.data
            }
        if isinstance(obj, set):
            return {
                'data': [[key, obj[key]] for key in obj.__iter__()]
            }
        return super(MyJSONEncoder, self).default(obj)


app = Flask(__name__)
app.config.from_object('config')
app.json_encoder = MyJSONEncoder


class CascadeBlockProcessor:
    def __init__(self):
        self.banner = Banner()
        self.cascade = Cascade(WSDL, AUTH, SITE_ID)
        self.hashes = set([])  # Set([])
        # todo: better names
        self.missing = []
        self.missing_locations = []
        self.new_hashes = set([])
        self.data = []

    def process_all_blocks(self, time_to_wait):
        # It should be noted that this only streams to Chrome; Firefox tries to download the JS as a file.

        def generator():
            data = self.banner.get_program_data()
            self.data = [row for row in data]
            r = requests.get(XML_URL)
            # Process the r.text to find the errant characters
            safe_text = unicodedata.normalize('NFKD', r.text).encode('ascii', 'ignore')
            block_xml = ET.fromstring(safe_text)
            blocks = []

            # Any blocks that begin with any of these paths will NOT by synced by this method
            paths_to_ignore = ["_shared-content/program-blocks/undergrad", "_shared-content/program-blocks/seminary"]

            for e in block_xml.findall('.//system-block'):
                block_id = e.get('id')
                block = Block(self.cascade, block_id)
                block_path = ast.literal_eval(block.read_asset())['asset']['xhtmlDataDefinitionBlock']['path']
                if any([path in block_path for path in paths_to_ignore]):
                    continue
                result = self.process_block(block_id)
                blocks.append(result)
                yield result + "\n"
                time.sleep(time_to_wait)
            yield "\nAll blocks have been synced."

        return Response(generator(), mimetype='text/json')

    def process_block_by_path(self, path):
        block_id = ast.literal_eval(Block(self.cascade, "/"+path).read_asset())['asset']['xhtmlDataDefinitionBlock']['id']
        return self.process_block_by_id(block_id)

    def process_block_by_id(self, id):
        result = self.process_block(id)

        self.check_hashes()

        if len(self.missing):

            caps_gs = []
            for code in self.missing:
                # will there be a 2- for some reason outside of a code?
                if '2-' in code:
                    caps_gs.append(code)

            caps_gs.insert(0, MISSING_DATA_MESSAGE + "<br/>")
            caps_gs.append("<br/>If you have any questions, please email web-services@bethel.edu.")

            send_message("No CAPS/GS Banner Data Found", "<br/>".join(caps_gs), html=True, caps_gs=True)

        self.cascade.publish(PUBLISHSET_ID, 'publishset')
        self.create_readers_digest()
        return result  # "%s" % "<br/>".join(self.hashes)

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

    def post_process_all(self):
        # compare hashes to SQL
        self.check_hashes()
        caps_gs = []
        if len(self.missing):
            for code in self.missing:
                # will there be a 2- for some reason outside of a code?
                if '2-' in code:
                    caps_gs.append(code)

        caps_gs.insert(0, MISSING_DATA_MESSAGE + "<br/>")
        caps_gs.append("<br/>If you have any questions, please email web-services@bethel.edu.")

        send_message("No CAPS/GS Banner Data Found", "<br/>".join(caps_gs), html=True, caps_gs=True)

        self.cascade.publish(PUBLISHSET_ID, 'publishset')
        self.create_readers_digest()

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
        results = []
        for row in self.data:
            if row['program_code'] == code:
                results.append(row)
        return results

    def check_hashes(self):
        data = self.data
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
        block_data = json.loads(program_block.read_asset())
        # Dates don't edit well
        my_path =  block_data['asset']['xhtmlDataDefinitionBlock']['path']
        for key in block_data['asset']['xhtmlDataDefinitionBlock'].keys():
            if key.endswith('Date'):
                del block_data['asset']['xhtmlDataDefinitionBlock'][key]

        block_properties = block_data['asset']['xhtmlDataDefinitionBlock']

        structured_data = block_properties['structuredData']

        if structured_data['definitionPath'] != "Blocks/Program":
            return my_path + " not in Blocks/Program"
        if 'seminary' in block_properties['path']:
            return my_path + " does not have 'seminary' in its path"

        nodes = structured_data['structuredDataNodes']['structuredDataNode']

        # mark the code down as "seen"
        try:
            program_hash = nodes[0]['structuredDataNodes']['structuredDataNode'][0]['text']
            self.hashes.add(program_hash)
        except KeyError:
            # not all programs have generic codes -- only concentration codes.
            pass

        for i, concentration_structure in enumerate(nodes):

            # every node after the first is a concentration
            if i == 0:
                continue

            concentration = concentration_structure['structuredDataNodes']['structuredDataNode']

            try:
                concentration_code = concentration[0]['text']
            except KeyError:
                continue

            # some have courses entered so the index isn't the same. use the last one
            banner_info = concentration[len(concentration)-1]['structuredDataNodes']['structuredDataNode']

            # load the data from banner for this code
            data = self.get_data_for_code(concentration_code)

            if not data:
                self.find(banner_info, 'concentration_name')['text'] = ""
                self.find(banner_info, 'cost')['text'] = ""
                details = self.find(banner_info, 'cohort_details')['structuredDataNodes']['structuredDataNode']
                for item in details:
                    item['text'] = ""

            # update block
            cohort_details = self.find_all(banner_info, 'cohort_details')
            # down to 1 delivery detail, in case any got removed. Just re-populate them all
            if len(cohort_details) > 1:
                for entry in range(1, len(cohort_details)):
                    banner_info.remove(cohort_details[entry])

            cohort_details = self.find_all(banner_info, 'cohort_details')

            j = None
            for j, row in enumerate(data):
                # concentration
                self.find(banner_info, 'concentration_name')['text'] = row['concentration_name']
                self.find(banner_info, 'cost')['text'] = "$%s" % row['cost_per_credit']

                # add a new detail for each row in the SQL result set.
                if len(cohort_details) <= j:
                    # Its going to be immediality overwritten by the new SQL row so it doesn't matter which node
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
                    location = ''

                self.find(details, 'location')['text'] = location

                # break up 'Fall 2015 - CAPS/GS' to 'Fall' and '2015'
                term, year = row['start_date'].split(' - ')[0].split(' ')
                self.find(details, 'semester_start')['text'] = term
                self.find(details, 'year_start')['text'] = year

            # consider 0 a good value as the first row in enumarate has j=0
            if j is None:
                self.missing.append(
                    """ %s (%s) """ % (block_properties['name'],  concentration_code)
                )
            else:
                # mark the code down as "seen"
                self.hashes.add(concentration_code)

        asset = {
            'xhtmlDataDefinitionBlock': block_data['asset']['xhtmlDataDefinitionBlock']
        }
        program_block.edit_asset(asset)
        return my_path + " successfully updated and synced"


@app.before_request
def before():
    session['send_email'] = False
    session['cpb'] = CascadeBlockProcessor()


@app.after_request
def after(response):
    if session['send_email']:
        session['cbp'].post_process_all()
    return response


class AdultProgramsView(FlaskView):
    def get(self):
        return render_template("sync_template.html")

    @route("/sync-all/<time_interval>")
    @route("/sync-all/<time_interval>/<send_email>")
    def sync_all(self, time_interval, send_email=False):
        time_interval = float(time_interval)
        if send_email:
            session['send_email'] = True
        return session['cpb'].process_all_blocks(time_interval)

    @route("/sync-one-id/<identifier>")
    def sync_one_id(self, identifier):
        return session['cpb'].process_block_by_id(identifier)

    @route("/sync-one-path/<path:path>")
    def sync_one_path(self, path):
        return session['cpb'].process_block_by_path(path)


AdultProgramsView.register(app)


if __name__ == "__main__":
    app.run(debug=True)
