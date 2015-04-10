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
from descriptions import delivery_descriptions
from config import WSDL, AUTH, SITE_ID, XML_URL
from descriptions import locations, length_type, labels


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

            # update block
            delivery_details = find_all(banner_info, 'concentration_details')
            # down to 1 delivery detail, in case any got removed. Just re-populate them all
            if len(delivery_details) > 1:
                for entry in range(1, len(delivery_details)):
                    banner_info.remove(delivery_details[entry])

            delivery_details = find_all(banner_info, 'concentration_details')

            found_results = False
            for j, row in enumerate(data):
                found_results = True
                # concentration
                find(banner_info, 'concentration_name')['text'] = row['concentration_name']
                find(banner_info, 'total_credits')['text'] = row['total_credits']
                # cost_per_credit = row['cost_per_credit']

                # add a new detail for each row in the SQL result set.
                if len(delivery_details) <= j:
                    # Its going to be immediality overwritten by the new SQL row so it doesn't matter which node
                    banner_info.append(copy.deepcopy(delivery_details[0]))
                    # re-populate the list with the new item added so we can select it
                    delivery_details = find_all(banner_info, 'concentration_details')

                details = delivery_details[j]['structuredDataNodes']['structuredDataNode']

                delivery_code = row['delivery_code']
                delivery_label = row['delivery_label']
                if not delivery_label:
                    delivery_label = labels[delivery_code]

                find(details, 'delivery_code')['text'] = delivery_code
                find(details, 'delivery_label')['text'] = delivery_label
                find(details, 'delivery_description')['text'] = delivery_descriptions[row['delivery_code']]

                location = locations[row['location']]
                if delivery_code in ['O', 'OO']:
                    location = ''

                find(details, 'location')['text'] = location
                find(details, 'start_date')['text'] = row['start_date'].split(' - ')[0]

                program_length = "%s %s" % (row['program_length'], length_type[row['length_unit']])
                find(details, 'program_length')['text'] = program_length

            if not found_results:
                # todo add email notification
                send_message("programs syc alert", "skipped %s" % concentration_code)
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