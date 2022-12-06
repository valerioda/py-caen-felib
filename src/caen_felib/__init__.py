"""
@defgroup Python Python
@brief Python wrapper

@pre This module requires CAEN FELib libraries to be installed in the system.
"""

__author__		= 'Giovanni Cerretani'
__copyright__	= 'Copyright (C) 2020-2022 CAEN SpA'
__license__		= 'LGPLv3+'
__version__		= '0.0'
__contact__		= 'https://www.caen.it/'

from caen_felib.lib import _Lib

# Initialize library
lib = _Lib('CAEN_FELib')
