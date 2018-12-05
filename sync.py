import ast
import copy
import json
import requests
import time
import unicodedata
import xml.etree.ElementTree as ET

from bu_cascade.assets.block import Block
from bu_cascade.cascade_connector import Cascade
from bu_cascade.asset_tools import find, update
from flask import Flask, render_template, Response, stream_with_context
from flask.ext.classy import FlaskView, route
from mail import send_message

from program_codes_to_skip import SKIP_CODES
from config import WSDL, AUTH, SITE_ID, STAGING_DESTINATION_ID, XML_URL, PUBLISHSET_ID


app = Flask(__name__)
app.config.from_object('config')

from raven.contrib.flask import Sentry
sentry = Sentry(app, dsn=app.config['RAVEN_URL'])


class CascadeBlockProcessor:
    def __init__(self):
        self.cascade = Cascade(WSDL, AUTH, SITE_ID, STAGING_DESTINATION_ID)
        self.codes_found_in_cascade = []
        self.missing_data_codes = []

    def process_all_blocks(self, time_to_wait, send_email_after):

        def generator(data, time_to_wait, send_email_after):
            yield "Beginning sync of all blocks" + "<br/><br/>"
            r = requests.get(XML_URL, headers={'Cache-Control': 'no-cache'})
            # Process the r.text to find the errant, non-ASCII characters
            safe_text = unicodedata.normalize('NFKD', r.text).encode('ascii', 'ignore')
            block_xml = ET.fromstring(safe_text)

            # todo: the 2nd one can be deleted when we launch
            paths_to_ignore = ["_shared-content/program-blocks/undergrad", "_shared-content/program-blocks-test/undergrad"]

            blocks = []
            for block in block_xml.findall('.//system-block'):
                if any([path in block.find('path').text for path in paths_to_ignore]):
                    continue

                result = self.process_block(data, block.get('id'))
                blocks.append(result)
                yield result + "<br/>"
                time.sleep(time_to_wait)
            yield "<br/>All blocks have been synced."

            # todo: don't publish for testing. When this goes live, add this line back in
            # self.cascade.publish(PUBLISHSET_ID, 'publishset')

            if send_email_after:
                missing_data_codes = self.missing_data_codes

                caps_gs_sem_email = render_template("caps_gs_sem_recipients_email.html", **locals())
                if len(missing_data_codes) > 0:
                    send_message("No CAPS/GS Banner Data Found", "<br/>".join(caps_gs_sem_email), html=True, caps_gs=True)

                unused_banner_codes = self.get_unused_banner_codes(data)
                admin_email = render_template("admin_email.html", **locals())
                send_message("Readers Digest: Program Sync", admin_email, html=True)

                # reset the codes found
                self.codes_found_in_cascade = []

        # load the data from banner for this code
        data = json.loads(requests.get('https://wsapi.bethel.edu/program-data').content)
        return Response(stream_with_context(generator(data, time_to_wait, send_email_after)), mimetype='text/html')

        # this method just passes through to process_block_by_id
    def process_block_by_path(self, path):
        block_id = ast.literal_eval(Block(self.cascade, "/"+path).asset)['xhtmlDataDefinitionBlock']['id']

        return self.process_block_by_id(block_id)

    def process_block_by_id(self, id):
        # load the data from banner for this code
        data = json.loads(requests.get('https://wsapi.bethel.edu/program-data').content)

        result = self.process_block(data, id)
        return result

    def get_unused_banner_codes(self, data):
        unused_banner_codes = []
        for index, data in data.iteritems():
            if data['prog_code'] not in self.codes_found_in_cascade and data['prog_code'] not in unused_banner_codes:
                unused_banner_codes.append(data['prog_code'])

        return unused_banner_codes

    def delete_and_clear_cohort_details(self, concentration):
        counter = 0
        for element in find(concentration, 'concentration_banner', False):
            if element['identifier'] == 'cohort_details':
                for to_clear in element['structuredDataNodes']['structuredDataNode']:
                    to_clear['text'] = ''

                # we use a break since we delete the amm down below
                break
            counter += 1

        # delete all after the one you exited at
        del find(concentration, 'concentration_banner', False)[counter + 1:]

        return True

    def process_block(self, data, block_id):
        program_block = Block(self.cascade, block_id)
        block_asset = program_block.asset

        block_path = find(block_asset, 'path', False)
        if find(block_asset, 'definitionPath', False) != "Blocks/Program":
            return block_path + " not in Blocks/Program"

        # gather concentrations
        concentrations = find(program_block.structured_data, 'concentration')
        if not isinstance(concentrations, list):
            concentrations = [concentrations]

        for concentration in concentrations:
            concentration_code = find(concentration, 'concentration_code', False)

            if concentration_code in SKIP_CODES:
                print "Code '%s' found in skip list; skipping it" % concentration_code
                continue

            self.delete_and_clear_cohort_details(concentration)

            # todo: remove this after launch
            ##################### Code to be used until after we launch! ##########################
            update(concentration, 'override-cohort-details', 'No')
            counter = 0
            for element in concentration['structuredDataNodes']['structuredDataNode']:
                if element['identifier'] == 'new-cohort-details-group':
                    for to_clear in element['structuredDataNodes']['structuredDataNode']:
                        to_clear['text'] = ''
                    del concentration['structuredDataNodes']['structuredDataNode'][counter]
                    # we have to subtract from the counter because indexes are off when we remove
                    counter -= 1
                counter += 1
            #######################################################################################

            banner_details_added = 0
            for index, row in data.iteritems():
                if row['prog_code'] != concentration_code:
                    continue

                # if you need more banner details, copy more!
                if banner_details_added != 0:
                    old_cohort = find(concentration, 'cohort_details')
                    if isinstance(old_cohort, list):
                        old_cohort = old_cohort[0]

                    new_cohort = copy.deepcopy(old_cohort)
                    find(concentration, 'concentration_banner', False).append(new_cohort)

                # set the new cohort details - if we get a list from bu_cascade, get the last element
                # The last element is the one we most recently added
                new_cohort_details = find(concentration, 'cohort_details')
                if isinstance(new_cohort_details, list):
                    new_cohort_details = new_cohort_details[-1]

                # clear values
                for to_clear in new_cohort_details['structuredDataNodes']['structuredDataNode']:
                    to_clear['text'] = ''

                # start dates or dynamic. Derek Sends us "000000" to denote that
                if row['start_term_code'] == u'000000':
                    update(new_cohort_details, 'cohort_start_type', "Dynamic")
                    update(new_cohort_details, 'dynamic_start_text', row['start_term_desc'])
                else:  # semester
                    update(new_cohort_details, 'cohort_start_type', "Semester")
                    update(new_cohort_details, 'semester_start', row['start_term_short_label'])
                    update(new_cohort_details, 'year_start', row['start_term_year_label'])

                # add data
                update(new_cohort_details, 'delivery_description', row['delivery_desc'])
                update(new_cohort_details, 'delivery_label', row['delivery_label'])
                update(new_cohort_details, 'delivery_subheading', row['delivery_sub_label'])
                update(new_cohort_details, 'location', row['loc_desc'])
                if row['prog_desc'] != '':
                    update(concentration, 'concentration_name', row['prog_desc'])

                banner_details_added += 1

            if banner_details_added == 0:
                print "No data found for program code %s, even though it's supposed to sync" % concentration_code
                self.missing_data_codes.append(
                    """ %s (%s) """ % (find(block_asset, 'name', False), concentration_code)
                )
            else:
                # mark the code down as "seen"
                self.codes_found_in_cascade.append(concentration_code)
        try:
            program_block.edit_asset(block_asset)
        except:
            sentry.captureException()
            return block_path + " failed to sync"

        return block_path + " successfully updated and synced"


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


# Legacy cost per credit code
# I kept this in hear, in case this ever gets added back in (caleb)
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