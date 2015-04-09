__author__ = 'ejc84332'

import copy
import requests
import json
import xml.etree.ElementTree as ET
from sets import Set

from banner import Banner
from flask import Flask
from flask.ext.classy import FlaskView

app = Flask(__name__)

from bu_cascade.cascade_connector import Cascade
from bu_cascade.assets.block import Block

from config import WSDL, AUTH, SITE_ID, XML_URL


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

        self.locations = {
            'SP': 'Saint Paul',
            'PT': 'Pine Tree',
            'RF': 'Red Fox',
            'SD': 'San Diego',
            'NOR': 'Normandale'
        }

        self.length_type = {
            'Y': 'years',
            'M': 'months',
            'W': "weeks"
        }

    def get(self):
            r = requests.get(XML_URL)
            block_xml = ET.fromstring(r.text)
            blocks = []
            for e in block_xml.findall('.//system-block'):
                block_id = e.get('id')
                blocks.append(self.process_block(block_id))

            return "<pre>%s</pre>" % "\n".join(self.hashes)

    def process_block(self, block_id):

        program_block = Block(self.cascade, block_id)
        block_data = json.loads(program_block.read_asset())
        # Dates don't edit well
        for key in block_data['asset']['xhtmlDataDefinitionBlock'].keys():
            if key.endswith('Date'):
                del block_data['asset']['xhtmlDataDefinitionBlock'][key]

        structured_data = block_data['asset']['xhtmlDataDefinitionBlock']['structuredData']

        if structured_data['definitionPath'] != "Blocks/Program":
            return False

        nodes = structured_data['structuredDataNodes']['structuredDataNode']

        # mark the code down as "seen"
        try:
            program_hash = nodes[0]['structuredDataNodes']['structuredDataNode'][0]['text']
        except KeyError:
            # todo email notoficatio
            return False
        self.hashes.add(program_hash)

        for i, concentration in enumerate(nodes):

            # every node after the first is a concentration
            if i == 0:
                continue

            concentration = concentration['structuredDataNodes']['structuredDataNode']

            concentration_code = concentration[0]['text']
            banner_info = concentration[3]['structuredDataNodes']['structuredDataNode']

            # load the data from banner for this code
            data = self.banner.get_program_data(concentration_code)


            p = 'syvprgmatt_'
            # update block
            delivery_details = find_all(banner_info, 'concentration_details')

            found_results = False
            for j, row in enumerate(data):
                found_results = True
                # concentration
                find(banner_info, 'concentration_name')['text'] = row[p+'program_desc']
                find(banner_info, 'total_credits')['text'] = row[p+'prg_hours']
                # cost_per_credit = row[p+'cost_per_credit']

                # Are more rows in the SQL than details in the Block?
                if len(delivery_details) <= j:
                    # clone a details node into a new slot to add the new info
                    # Its going to be immediality overwritten by the new SQL row so it doesn't matter which node
                    banner_info.append(copy.deepcopy(find(banner_info, 'concentration_details')))
                    # re-populate the list with the new item added so we can select it
                    delivery_details = find_all(banner_info, 'concentration_details')

                details = delivery_details[j]['structuredDataNodes']['structuredDataNode']

                find(details, 'delivery_code')['text'] = row[p+'format_1']
                find(details, 'delivery_label')['text'] = row[p+'format_desc']
                find(details, 'location')['text'] = self.locations[row[p+'camp_code_1']]
                find(details, 'start_date')['text'] = row[p+'term_code_start'].split(' - ')[0]

                program_length = "%s %s" % (row[p+'prg_length'], self.length_type[row[p+'prg_length_type']])
                find(details, 'program_length')['text'] = program_length

            if not found_results:
                # todo add email notification
                print "skipped %s" % concentration_code
            else:
                # mark the code down as "seen"
                self.hashes.add(concentration_code)

        asset = {
            'xhtmlDataDefinitionBlock': block_data['asset']['xhtmlDataDefinitionBlock']
        }
        program_block.edit_asset(asset)
        return True

AdultProgramsView.register(app)


if __name__ == "__main__":
    app.run(debug=True)