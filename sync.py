# Imports from global Python packages
import ast
import copy
import hashlib
import json
import math
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
from raven.contrib.flask import Sentry

# Imports from elsewhere in this project
from mail import send_message
from config import WSDL, AUTH, SITE_ID, STAGING_DESTINATION_ID, XML_URL


app = Flask(__name__)
app.config.from_object('config')

sentry = Sentry(app, dsn=app.config['RAVEN_URL'])


class CascadeBlockProcessor:
    def __init__(self):
        self.cascade = Cascade(WSDL, AUTH, SITE_ID, STAGING_DESTINATION_ID)
        self.codes_found_in_cascade = []
        self.missing_data_codes = []

    def convert_dictionary_to_hash(self, dictionary):

        def _recursively_alphabetize_dictionary_by_keys(dictionary_to_alphabetize):
            if isinstance(dictionary_to_alphabetize, dict):
                to_return = '{'
                ordered_keys = sorted(dictionary_to_alphabetize.keys())
                for key in ordered_keys:
                    to_return += "'%s': " % str(key)
                    value = dictionary_to_alphabetize[key]
                    if isinstance(value, dict):
                        to_return += _recursively_alphabetize_dictionary_by_keys(value)
                    elif isinstance(value, (str, unicode)):
                        to_return += "'%s'" % str(value)
                    to_return += ', '

                return to_return + '}'
            else:
                return 'Not a dictionary'

        alphabetized_string = _recursively_alphabetize_dictionary_by_keys(dictionary)

        return repr(hashlib.md5(alphabetized_string).digest())

    def get_changed_banner_rows(self):
        """
        In order for this method to work properly, the rows of data returned by Banner via WSAPI must be sorted
        alphabetically by program code. If the rows are parsed out of order, the tertiary check (row index) will
        produce errant results. Thankfully the sorting is already done by WSAPI before the data gets sent here.
        """

        # First, read in dictionary of old {row_keys: md5 hashes} from .csv
        # Disclaimer: md5 hashes can contain commas, so the separator is technically ',\t'
        filepath = '/Users/phg49389/Sites/programs/bannerDataHashes.csv'  # Has to be absolute path, not relative
        old_hashes = {}
        if os.path.isfile(filepath):
            with open(filepath, 'r') as old_data_hashes:
                for line in old_data_hashes.readlines():
                    vals = line.split(',\t')
                    old_hashes[vals[0]] = vals[1]

        # Second, read in current values of data from Banner via WSAPI
        # These are sorted twice in WSAPI: first by prog_code, and then sub-sorted by start_term_code
        new_banner_data = json.loads(requests.get('https://wsapi.bethel.edu/program-data').content)
        # Sourced from: https://stackoverflow.com/a/16839304
        data_len_magnitude = int(math.log10(len(new_banner_data)))

        def _leftpad_index(index, mag):
            if index == 0:
                # Can't take the log of 0
                index_magnitude = 0
            else:
                # Take in an integer i, and left-pad zeroes until it has as many decimal places as specified by "mag"
                index_magnitude = int(math.log10(index))
            return '0' * (mag - index_magnitude) + str(index)

        # Third, create a dictionary of new md5 hashes, each stored at a 3-part key:
        # prog_code, start_term_code, and row index
        new_hashes = {}
        for i in range(len(new_banner_data)):
            row = new_banner_data[i]
            # Have to left-pad the index with zeroes so that sorted() retains proper index order
            row_key = row['prog_code'] + '__' + row['start_term_code'] + '__' + _leftpad_index(i, data_len_magnitude)
            new_hashes[row_key] = self.convert_dictionary_to_hash(row)

        # This is only true on the first run, so the file has to be created
        if not os.path.isfile(filepath):
            with open(filepath, 'w+') as new_data_hashes:
                for row_key in sorted(new_hashes.keys()):
                    new_data_hashes.write('%s,\t%s,\t\n' % (row_key, new_hashes[row_key]))

            # Return all rows as "new"
            return new_banner_data

        # Fourth, check if any md5 hashes in new are different than in old, or if new has md5 hashes that old doesn't.
        # If there are any differences, put the corresponding rows into an array that will eventually be returned.
        # Due to how the rows are sorted in WSAPI and how the row_key is generated for the dictionaries, the indexes
        # at the end of each row_key should be in order from 0-n. This is CRUCIAL to the sorting algorithm below!
        old_index = 0
        old_keys = sorted(old_hashes.keys())
        new_index = 0
        new_keys = sorted(new_hashes.keys())

        # This variable is an offset to help compare new row indexes to old row indexes
        # A positive value means there have been more rows added to new than rows deleted from old, and vice versa
        num_rows_inserted_or_deleted = 0
        different_or_new_rows = []

        while old_index < len(old_keys) and new_index < len(new_keys):
            old_key = old_keys[old_index]
            old_program_code, old_start_term_code, old_row_index = old_key.split('__')
            old_row_index = int(old_row_index)
            new_key = new_keys[new_index]
            new_program_code, new_start_term_code, new_row_index = new_key.split('__')
            new_row_index = int(new_row_index)

            """
            For all three of these checks, this is the logic:
            
                If old is alphanumerically before new, that implies that the corresponding old row was deleted. 
                Increment old_index, subtract from the row index offset variable, and move on.
                
                If old is alphanumerically after new, that implies that the corresponding new row was inserted.
                Increment new_index, add to the row index offset variable, and append the new row.
                
                If old == new, then move on to the next layer of checks.
                
            Finally, if all three values of the old and the new keys are equal, then compare the values of the hashes.
            If the hashes are different, then the row has changed. Append the changed row, and increment both 
            old_index and new_index.
            """
            if old_program_code < new_program_code:
                old_index += 1
                num_rows_inserted_or_deleted -= 1
            elif old_program_code > new_program_code:
                different_or_new_rows.append(new_banner_data[new_row_index])
                new_index += 1
                num_rows_inserted_or_deleted += 1
            else:  # old_program_code == new_program_code
                # If the prog_codes are the same, compare by start_term_code
                if old_start_term_code < new_start_term_code:
                    old_index += 1
                    num_rows_inserted_or_deleted -= 1
                elif old_start_term_code > new_start_term_code:
                    different_or_new_rows.append(new_banner_data[new_row_index])
                    new_index += 1
                    num_rows_inserted_or_deleted += 1
                else:  # old_start_term_code == new_start_term_code
                    # If both prog_codes and start_term_codes are the same, check the row indexes
                    if old_row_index + num_rows_inserted_or_deleted < new_row_index:
                        old_index += 1
                        num_rows_inserted_or_deleted -= 1
                    elif old_row_index + num_rows_inserted_or_deleted > new_row_index:
                        different_or_new_rows.append(new_banner_data[new_row_index])
                        new_index += 1
                        num_rows_inserted_or_deleted += 1
                    else:  # old_row_index == new_row_index
                        # If all values of the old row_key and new row_key are the same, finally we can compare hashes
                        old_hash = old_hashes[old_key]
                        new_hash = new_hashes[new_key]
                        if old_hash != new_hash:
                            # The hashes, and therefore the content of the rows, are different
                            different_or_new_rows.append(new_banner_data[new_row_index])

                        old_index += 1
                        new_index += 1

        # Edge cases: it's possible that the while loop above may end before iterating over all keys in both arrays
        # We can discard any keys from the old list since they just represent rows of data that were deleted, but
        # any keys remaining the new list represent rows that have been inserted between runs
        for i in range(new_index, len(new_keys)):
            new_key = new_keys[new_index]
            new_row_index = int(new_key.split('__')[2])
            different_or_new_rows.append(new_banner_data[new_row_index])

        # Fifth, write the new dictionary to the .csv
        with open(filepath, 'w+') as new_data_hashes:
            for row_key in sorted(new_hashes.keys()):
                new_data_hashes.write('%s,\t%s,\t\n' % (row_key, new_hashes[row_key]))

        # Finally, return the array of different rows for use in block processing
        return different_or_new_rows

    def process_all_blocks(self, time_to_wait, send_email_after):
        changed_rows = self.get_changed_banner_rows()

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

            result = self.process_block(changed_rows, block_id)
            blocks.append(result)
            time.sleep(time_to_wait)

        if send_email_after:
            missing_data_codes = self.missing_data_codes

            caps_gs_sem_email_content = render_template('caps_gs_sem_recipients_email.html', **locals())
            if len(missing_data_codes) > 0:
                send_message('No CAPS/GS Banner Data Found', caps_gs_sem_email_content, html=True, caps_gs_sem=True)

            unused_banner_codes = self.get_unused_banner_codes(changed_rows)
            caps_gs_sem_recipients = app.config['CAPS_GS_SEM_RECIPIENTS']
            admin_email_content = render_template('admin_email.html', **locals())
            send_message('Readers Digest: Program Sync', admin_email_content, html=True)

            # reset the codes found
            self.codes_found_in_cascade = []
            
        return 'Finished sync of all CAPS/GS/SEM programs.'

    # this method just passes through to process_block_by_id
    def process_block_by_path(self, path):
        block_id = ast.literal_eval(Block(self.cascade, '/'+path).asset)['xhtmlDataDefinitionBlock']['id']

        return self.process_block_by_id(block_id)

    def process_block_by_id(self, id):
        changed_rows = self.get_changed_banner_rows()

        return self.process_block(changed_rows, id)

    # we gather unused banner codes to send report emails after the sync
    def get_unused_banner_codes(self, data):
        unused_banner_codes = []
        for data in data:  # removed '.iteritems()', as it was throwing an error.
            if data['prog_code'] not in self.codes_found_in_cascade and data['prog_code'] not in unused_banner_codes and data['prog_code'] not in app.config['SKIP_CONCENTRATION_CODES']:
                unused_banner_codes.append(data['prog_code'])
                print data['prog_code']

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

    def process_block(self, data, block_id):
        this_block_had_a_concentration_updated = False

        program_block = Block(self.cascade, block_id)
        block_asset = program_block.asset

        block_path = find(block_asset, 'path', False)
        if find(block_asset, 'definitionPath', False) != 'Blocks/Program':
            return block_path + ' not in Blocks/Program'

        if block_id in app.config['SKIP_CONCENTRATION_CODES']:
            return block_path + ' is currently being skipped.'

        # gather concentrations
        concentrations = find(program_block.structured_data, 'concentration')
        if not isinstance(concentrations, list):
            concentrations = [concentrations]

        for concentration in concentrations:
            concentration_code = find(concentration, 'concentration_code', False)

            self.delete_and_clear_cohort_details(concentration)

            banner_details_added = 0
            for row in data:  # removed '.iteritems()', as it was throwing an error.
                if row['prog_code'] != concentration_code:
                    continue

                this_block_had_a_concentration_updated = True

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
                print "No data found for program code %s, even though it's supposed to sync" % concentration_code
                self.missing_data_codes.append(
                    """ %s (%s) """ % (find(block_asset, 'name', False), concentration_code)
                )
            else:
                # mark the code down as "seen"
                self.codes_found_in_cascade.append(concentration_code)

        if this_block_had_a_concentration_updated:
            if not app.config['DEVELOPMENT']:
                try:
                    # we are getting the concentration path and publishing out the applicable
                    # program details folder and program index page.
                    concentration_page_path = find(concentrations[0], 'concentration_page', False).get('pagePath')
                    program_folder = '/' + concentration_page_path[:concentration_page_path.find('program-details')]
                    # 1) publish the program-details folder
                    # self.cascade.publish(program_folder + 'program-details', 'folder')

                    # 2) publish the program index
                    # self.cascade.publish(program_folder + 'index', 'page')
                except:
                    sentry.captureException()

            try:
                # program_block.edit_asset(block_asset)
                pass
            except:
                sentry.captureException()
                return block_path + ' failed to sync'

            return block_path + ' successfully updated and synced'
        else:
            return block_path + " didn't have any data updated in Banner"


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


AdultProgramsView.register(app)


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