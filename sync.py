__author__ = 'ejc84332'

import copy
import requests
import json
import xml.etree.ElementTree as ET
from sets import Set

from banner import Banner
from flask import Flask
from flask.ext.classy import FlaskView
from flask.ext.mail import Mail

app = Flask(__name__)

from bu_cascade.cascade_connector import Cascade
from bu_cascade.assets.block import Block

from mail import send_message
from config import WSDL, AUTH, SITE_ID, XML_URL, PUBLISHSET_ID, MISSING_DATA_MESSAGE
from descriptions import delivery_descriptions, locations, labels, subheadings

from flask import render_template

def find(search_list, key):
    for item in search_list:
        if item['identifier'] == key:
            return item


def find_all(search_list, key):
    matches = []
    for item in search_list:
        if item['identifier'] == key:
            matches.append(item)

    return matches


class AdultProgramsView(FlaskView):

    def __init__(self):

        self.banner = Banner()
        self.cascade = Cascade(WSDL, AUTH, SITE_ID)
        self.hashes = Set([])
        # todo: better name
        self.missing = []
        self.missing_locations = []
        self.new_hashes = []
        self.data = []

    def get(self):
            data = self.banner.get_program_data()
            self.data = [row for row in data]
            r = requests.get(XML_URL)
            block_xml = ET.fromstring(r.text)
            blocks = []
            i = 0
            for e in block_xml.findall('.//system-block'):
                # if i:
                #     continue
                block_id = e.get('id')

                blocks.append(self.process_block(block_id))
                i += 1
            # compare hashes to SQL
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
            return "<pre>%s</pre>" % "\n".join(self.hashes)

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
        for key in block_data['asset']['xhtmlDataDefinitionBlock'].keys():
            if key.endswith('Date'):
                del block_data['asset']['xhtmlDataDefinitionBlock'][key]

        block_properties = block_data['asset']['xhtmlDataDefinitionBlock']

        structured_data = block_properties['structuredData']

        if structured_data['definitionPath'] != "Blocks/Program":
            return False

        if 'seminary' in block_properties['path']:
            return False

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
                find(banner_info, 'concentration_name')['text'] = ""
                find(banner_info, 'cost')['text'] = ""
                details = find(banner_info, 'cohort_details')['structuredDataNodes']['structuredDataNode']
                for item in details:
                    item['text'] = ""

            # update block
            cohort_details = find_all(banner_info, 'cohort_details')
            # down to 1 delivery detail, in case any got removed. Just re-populate them all
            if len(cohort_details) > 1:
                for entry in range(1, len(cohort_details)):
                    banner_info.remove(cohort_details[entry])

            cohort_details = find_all(banner_info, 'cohort_details')

            j = None
            for j, row in enumerate(data):
                # concentration
                find(banner_info, 'concentration_name')['text'] = row['concentration_name']
                find(banner_info, 'cost')['text'] = "$%s" % row['cost_per_credit']

                # add a new detail for each row in the SQL result set.
                if len(cohort_details) <= j:
                    # Its going to be immediality overwritten by the new SQL row so it doesn't matter which node
                    banner_info.append(copy.deepcopy(cohort_details[0]))
                    # re-populate the list with the new item added so we can select it
                    cohort_details = find_all(banner_info, 'cohort_details')

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



                find(details, 'delivery_description')['text'] = delivery_row_code
                find(details, 'delivery_label')['text'] = delivery_label

                # adding delivery sub headings
                try:
                    delivery_subheadings = subheadings[delivery_code]
                except KeyError:
                    delivery_subheadings = ""
                if find(details, 'delivery_subheading'):
                    find(details, 'delivery_subheading')['text'] = delivery_subheadings
                else:
                    details.append({'text': delivery_subheadings, 'identifier': 'delivery_subheading', 'type': 'text'})

                try:
                    location = locations[row['location']]
                except KeyError:
                    self.missing_locations.append(row['location'])

                if delivery_code in ['O', 'OO']:
                    location = ''

                find(details, 'location')['text'] = location

                # break up 'Fall 2015 - CAPS/GS' to 'Fall' and '2015'
                term, year = row['start_date'].split(' - ')[0].split(' ')
                find(details, 'semester_start')['text'] = term
                find(details, 'year_start')['text'] = year

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
        results = program_block.edit_asset(asset)
        print results
        return True

AdultProgramsView.register(app)


if __name__ == "__main__":
    app.run(debug=True)