# Imports from global Python packages
import ast
import copy
import datetime
import hashlib
import json
import os
import time
import unicodedata
import xml.etree.ElementTree as ET

# Imports from packages installed in the virtual environment
import requests
from bu_cascade.assets.block import Block
from bu_cascade.cascade_connector import Cascade
from bu_cascade.asset_tools import find, update
from flask import Flask, render_template
from flask_classy import FlaskView, route
import sentry_sdk

# Imports from elsewhere in this project
from mail import send_message
from config import WSDL, AUTH, SITE_ID, STAGING_DESTINATION_ID, XML_URL


app = Flask(__name__)
app.config.from_object('config')

if app.config['SENTRY_URL']:
    from sentry_sdk.integrations.flask import FlaskIntegration
    sentry_sdk.init(dsn=app.config['SENTRY_URL'], integrations=[FlaskIntegration()])


class CascadeBlockProcessor:
    def __init__(self):
        self.cascade = Cascade(WSDL, AUTH, SITE_ID, STAGING_DESTINATION_ID)
        self.codes_found_in_cascade = []
        self.codes_not_found_in_banner = []

    def get_new_banner_data(self):
        return json.loads(requests.get('https://wsapi.bethel.edu/program-data').content)

    def process_all_blocks(self, time_to_wait, send_email_after):
        new_banner_data = self.get_new_banner_data()

        if len(new_banner_data) == 0:
            return 'Received no data from banner; skipping all blocks'

        r = requests.get(XML_URL, headers={'Cache-Control': 'no-cache'})
        # Process the r.text to find the errant, non-ASCII characters
        safe_text = unicodedata.normalize('NFKD', r.text).encode('ascii', 'ignore')
        block_xml = ET.fromstring(safe_text)

        paths_to_ignore = ['_shared-content/program-blocks/undergrad']

        blocks = []
        for block in block_xml.findall('.//system-block'):
            if any([path in block.find('path').text for path in paths_to_ignore]):
                continue

            block_id = block.get('id')

            # gather codes that are in cascade
            concentration_code = block.find('.//concentration_code').text
            if concentration_code not in self.codes_found_in_cascade:
                self.codes_found_in_cascade.append(concentration_code)

            result = self.process_block(new_banner_data, block_id, time_to_wait)
            blocks.append(result)

        if send_email_after:
            codes_not_found_in_banner = self.codes_not_found_in_banner
            caps_gs_sem_email_content = render_template('caps_gs_sem_recipients_email.html', **locals())
            if len(codes_not_found_in_banner) > 0:
                send_message('No CAPS/GS Banner Data Found', caps_gs_sem_email_content, html=True, caps_gs_sem=True)

            unused_banner_codes = self.get_unused_banner_codes(new_banner_data)
            caps_gs_sem_recipients = app.config['CAPS_GS_SEM_RECIPIENTS']
            admin_email_content = render_template('admin_email.html', **locals())

            if codes_not_found_in_banner or unused_banner_codes:
                send_message('Readers Digest: Program Sync', admin_email_content, html=True)

        # reset the codes found
        self.codes_found_in_cascade = []
        self.codes_not_found_in_banner = []

        # publish program feeds
        self.cascade.publish(app.config['PUBLISHSET_ID'], 'publishset')

        return 'Finished sync of all CAPS/GS/SEM programs.'

    # log any new program code
    def log_concentration_codes(self, changed_banner_data):
        with open(app.config['BANNER_CHANGED_DATA_LOG'], mode='a') as file:
            for concentration in changed_banner_data:
                file.write('{} - New concentration code: {}\n'.format(datetime.datetime.now(), concentration.get('prog_code')))

    # this method just passes through to process_block_by_id
    def process_block_by_path(self, path):
        block_id = ast.literal_eval(Block(self.cascade, '/'+path).asset)['xhtmlDataDefinitionBlock']['id']

        return self.process_block_by_id(block_id)

    def process_block_by_id(self, id):
        # syncing a single block currently doesn't write the audit hashes or publish the program feeds
        new_banner_data = self.get_new_banner_data()

        return self.process_block(new_banner_data, id, 1)

    # we gather unused banner codes to send report emails after the sync
    def get_unused_banner_codes(self, new_banner_data):
        unused_banner_codes = []
        for data in new_banner_data:  # removed '.iteritems()', as it was throwing an error.
            # skip certain concentration codes.
            if data['prog_code'] in app.config['SKIP_CONCENTRATION_CODES']:
                continue
            # skip if its already added
            elif data['prog_code'] in unused_banner_codes:
                continue
            # otherwise, add it if its from banner, but not found in cascade
            elif data['prog_code'] not in self.codes_found_in_cascade:
                unused_banner_codes.append(data['prog_code'])

        return unused_banner_codes

    def delete_and_clear_cohort_details(self, concentration):
        counter = 0
        for element in find(concentration, 'concentration_banner', False):
            if element['identifier'] == 'cohort_details':
                for to_clear in element['structuredDataNodes']['structuredDataNode']:
                    to_clear['text'] = ''

                # we use a break since we delete them all down below
                break
            counter += 1

        # delete all cohort details after the last one iterated in the for loop
        del find(concentration, 'concentration_banner', False)[counter + 1:]

        return True

    def process_block(self, banner_data, block_id, time_to_wait=1):

        program_block = Block(self.cascade, block_id)
        block_asset = program_block.asset

        block_path = find(block_asset, 'path', False)

        if find(block_asset, 'definitionPath', False) != 'Blocks/Program':
            return block_path + ' not in Blocks/Program'

        if block_id in app.config['SKIP_BLOCK_IDS']:
            return block_path + ' is currently being skipped.'

        # gather concentrations
        concentrations = find(program_block.structured_data, 'concentration')
        if not isinstance(concentrations, list):
            concentrations = [concentrations]

        for concentration in concentrations:
            concentration_code = find(concentration, 'concentration_code', False)

            self.delete_and_clear_cohort_details(concentration)

            banner_details_added = 0
            for row in banner_data:  # removed '.iteritems()', as it was throwing an error.
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

                # start dates or dynamic. Derek Sends us '000000' to denote that
                if row['start_term_code'] == u'000000':
                    update(new_cohort_details, 'cohort_start_type', 'Dynamic')

                    # if we can't find a 'dynamic_start_text', then we will have to manually add the dict in
                    if not find(new_cohort_details, 'dynamic_start_text'):
                        new_cohort_details['structuredDataNodes']['structuredDataNode'].append(
                            {'type': 'text', 'identifier': 'dynamic_start_text', 'text': ''})
                    update(new_cohort_details, 'dynamic_start_text', row['start_term_desc'])
                else:  # semester
                    update(new_cohort_details, 'cohort_start_type', 'Semester')
                    update(new_cohort_details, 'semester_start', row['start_term_short_label'].strip())
                    update(new_cohort_details, 'year_start', row['start_term_year_label'].strip())

                # add data
                update(new_cohort_details, 'delivery_description', row['delivery_desc'])
                update(new_cohort_details, 'delivery_label', row['delivery_label'])
                update(new_cohort_details, 'delivery_subheading', row['delivery_sub_label'])
                update(new_cohort_details, 'location', row['loc_desc'])
                if row['prog_desc'] != '':
                    update(concentration, 'concentration_name', row['prog_desc'])

                banner_details_added += 1

            if banner_details_added == 0:
                print("No data found for program code %s, even though it's supposed to sync" % concentration_code)
                self.codes_not_found_in_banner.append(
                    """ %s (%s) """ % (find(block_asset, 'name', False), concentration_code)
                )
            else:
                # mark the code down as "seen"
                self.codes_found_in_cascade.append(concentration_code)
        try:
            program_block.edit_asset(block_asset)

            if not app.config['DEVELOPMENT']:
                # we are getting the concentration path and publishing out the applicable
                # program details folder and program index page.
                concentration_page_path = find(concentrations[0], 'concentration_page', False).get('pagePath')
                program_folder = '/' + concentration_page_path[:concentration_page_path.find('program-details')]
                # 1) publish the program-details folder
                self.cascade.publish(program_folder + 'program-details', 'folder')

                # 2) publish the program index
                self.cascade.publish(program_folder + 'index', 'page')
            time.sleep(time_to_wait)
        except:
            sentry_sdk.capture_exception()
            return block_path + ' failed to sync'

        return block_path + ' successfully updated and synced'


class AdultProgramsView(FlaskView):
    def __init__(self):
        self.cbp = CascadeBlockProcessor()

    def index(self):
        return render_template('sync_template.html')

    @route('/sync-all/<time_interval>')
    @route('/sync-all/<time_interval>/<send_email>')
    def sync_all(self, time_interval, send_email=False):
        time_interval = float(time_interval)
        send_email = bool(send_email)

        return self.cbp.process_all_blocks(time_interval, send_email)

    @route('/sync-one-id/<identifier>')
    def sync_one_id(self, identifier):
        return self.cbp.process_block_by_id(identifier)

    @route('/sync-one-path/<path:path>')
    def sync_one_path(self, path):
        return self.cbp.process_block_by_path(path)


AdultProgramsView.register(app, route_base="/")


# Legacy cost per credit code
# I kept this in here, in case this ever gets added back in (caleb)
"""
    if row['cost_per_credit']:
        self.find(banner_info, 'cost')['text'] = '$' + str(row['cost_per_credit'])
    else:
        print 'Row missing cost per credit:', row
        print 'Attempting to get manual price per credit'
        if row['program_code'] in MANUAL_COST_PER_CREDITS:
            print 'Code found in MANUAL_COST_PER_CREDITS; using that.'
            self.find(banner_info, 'cost')['text'] = MANUAL_COST_PER_CREDITS[row['program_code']]
        elif row['program_code'] in MISSING_CODES:
            print 'Code found in MISSING_CODES, so it's ok if it isn't synced'
        else:
            print 'Code not found in either manual list; THIS IS A REALLY BIG PROBLEM!'
        print ""
    def format_price_range(int_low, int_high):
        locale.setlocale(locale.LC_ALL, 'en_US')
        low = locale.format('%d', int_low, grouping=True)
        high = locale.format('%d', int_high, grouping=True)
        if int_low == int_high:
            return '$' + low
        else:
            return '$' + low + ' - ' + high
    
    # Derek said that if there's a min cost, there will also be a max cost. If they're different, then make
    # it a range. If they're the same, then it's a fixed cost.
    if row.get('min_cred_cost') and row.get('max_cred_cost'):
        min_credit = row.get('min_cred_cost')
        max_credit = row.get('max_cred_cost')
        self.find(banner_info, 'cost')['text'] = format_price_range(min_credit, max_credit)
    
    if row.get('min_prog_cost') and row.get('max_prog_cost'):
        min_program = row.get('min_prog_cost')
        max_program = row.get('max_prog_cost')
        self.find(banner_info, 'concentration_cost')['text'] = format_price_range(min_program, max_program)
    
    def format_price_range(int_low, int_high):
        locale.setlocale(locale.LC_ALL, 'en_US')
        low = locale.format('%d', int_low, grouping=True)
        high = locale.format('%d', int_high, grouping=True)
        if int_low == int_high:
            return '$' + low
        else:
            return '$' + low + ' - ' + high
    
    # Derek said that if there's a min cost, there will also be a max cost. If they're different, then make
    # it a range. If they're the same, then it's a fixed cost.
    if row.get('min_cred_cost') and row.get('max_cred_cost'):
        min_credit = row.get('min_cred_cost')
        max_credit = row.get('max_cred_cost')
        self.find(banner_info, 'cost')['text'] = format_price_range(min_credit, max_credit)
    
    if row.get('min_prog_cost') and row.get('max_prog_cost'):
        min_program = row.get('min_prog_cost')
        max_program = row.get('max_prog_cost')
        self.find(banner_info, 'concentration_cost')['text'] = format_price_range(min_program, max_program)
"""