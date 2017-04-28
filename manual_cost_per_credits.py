# Per David Freed 4/27/2017, these cost per credits should be accurate for use as backups if Banner doesn't have data
# for these codes.
MANUAL_COST_PER_CREDITS = {
    "2-AA-GESA": "$430",
    "2-AS-BULA": "$430",
    "2-AS-INDA": "$430",
    "2-BA-CHMP": "$430",
    "2-BA-COSP": "$485",
    "2-BA-HUSP": "$430",
    "2-BA-O-CHMC": "$430",
    "2-BA-O-HUSC": "$430",
    "2-BA-ORLP": "430",
    "2-BS-ACCT": "$430",
    "2-BS-AVIP": "$430",
    "2-BS-B-GBUS": "$430",
    "2-BS-B-MGMT": "$430",
    "2-BS-FINP": "$430",
    "2-BS-MISP": "$430",
    "2-BS-NURP": "$430-495",
    "2-CRT-CAMH": "$560",
    "2-CRT-CENV": "$535",
    "2-CRT-CGER": "$520",
    "2-CRT-CITL": "$535",
    "2-CRT-CLDR": "$695",
    "2-CRT-CNRE": "$590",
    "2-CRT-CSTM": "$535",
    "2-CRT-UG-AD": "$430",
    "2-CRT-UG-DC": "$430",
    "2-EDD-LHED": "$750",
    "2-EDD-LKAD": "$750",
    "2-LIC-E-DSPD": "$750",
    "2-LIC-E-PRID": "$750",
    "2-LIC-E-SUPD": "$750",
    "2-LIC-S-ABSQ": "$535",
    "2-LIC-S-ABST": "$535",
    "2-LIC-S-ASDG": "$535",
    "2-LIC-S-ASDQ": "$535",
    "2-LIC-S-DCDL": "$535",
    "2-LIC-S-DCDQ": "$535",
    "2-LIC-S-EBDG": "$535",
    "2-LIC-S-EBDQ": "$535",
    "2-LIC-T-ARTS": "$535",
    "2-LIC-T-BUED": "$535",
    "2-LIC-T-CHEM": "$535",
    "2-LIC-T-HEAL": "$535",
    "2-LIC-T-LIFE": "$535",
    "2-LIC-T-LITR": "$535",
    "2-LIC-T-MATH": "$535",
    "2-LIC-T-PHYS": "$535",
    "2-LIC-T-SCIE": "$535",
    "2-LIC-T-SOCS": "$535",
    "2-LIC-T-TEAQ": "$535",
    "2-LIC-T-TESL": "$535",
    "2-LIC-T-WRLD": "$535",
    "2-LIC-TCKT": "$535",
    "2-LIC-TWBL": "$535",
    "2-MA-COUG": "$560",
    "2-MA-C-CAMH": "$560",
    "2-MA-C-CMTY": "$560",
    "2-MA-E-ATSD": "$535",
    "2-MA-E-CUST": "$535",
    "2-MA-E-EDLE": "$535",
    "2-MA-E-ENVY": "$535",
    "2-MA-E-INBE": "$535",
    "2-MA-E-SPED": "$535",
    "2-MA-E-STME": "$535",
    "2-MA-E-TCED": "$535",
    "2-MA-E-TWBL": "$535", 
    "2-MA-GERG": "$520",
    "2-MA-S-ABST": "$535",
    "2-MA-S-ASDG": "$535",
    "2-MA-S-DCDL": "$535",
    "2-MA-S-EBDG": "$535",
    "2-MA-S-LEDG": "$535",
    "2-MA-SLDG": "$450-69",
    "2-MA-SPEG": "$535",
    "2-MA-TEAG": "$535",
    "2-MBA-B-FINA": "$695",
    "2-MBA-B-GLOB": "$695",
    "2-MBA-B-MGMT": "$695",
    "2-MS-MIDW": "$773",
    "2-MS-NEDG": "$590",
    "2-MS-PASG": "$773",
    "2-MS-N-NRLG": "$590",
    "2-BA-O-HRMC": "$430",
    "2-BS-B-HRMA": "$430",
}

# Per David Freed 4/27/2017, these codes either don't have a block in Cascade to sync or they don't have an associated
# degree to earn, so either way it shouldn't matter if they aren't synced.
MISSING_CODES = [
    "2-ADDW",
    "2-ADWG",
    "2-MA-ATLG",
    "2-MA-O-CSCL",
    "2-MS-ATRG",
    "2-NONDD",
    "2-NONDG",
    "2-NONDP",
    "2-PDP",
    "2-PREQG",
    "2-UNDMG",
    "2-UNDPC",
    "2-CRT-UG-HR",
]
