# -*- coding: utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck Professional — 专有商业软件，保留一切权利（Proprietary Software, All Rights Reserved）.
# See LICENSE for full license text.
#

import logging

def getlogger():
    # logger
    #logger = logging.getLogger(__name__)
    logger = logging.getLogger()
    if not logger.handlers:    
        logger.setLevel(logging.DEBUG)
        # create console handler and set level to debug
        #ch = logging.StreamHandler()
        ch = logging.FileHandler(r'autoDoc.log')
        ch.setLevel(logging.DEBUG)
    # create formatter
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    # add formatter to ch
        ch.setFormatter(formatter)
    # add ch to logger
        logger.addHandler(ch)
    return logger