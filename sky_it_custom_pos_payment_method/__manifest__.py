# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
{
    'name': 'Pos Payment Method Custom',
    'version': '14.0.1',
    'author': 'OdooPS',
    'category': '',
    'summary': 'Pos Payment Method Custom',
    'description': """
         Task ID - 2658357
        - Fees and Interests for Follow Up Reports.
    """,
    'depends': [
        'point_of_sale',
        'l10n_in',
    ],
    'data': [
        'views/pos_payment_method_views.xml',
    ],
    'qweb': [],
    'installable': True,

}
