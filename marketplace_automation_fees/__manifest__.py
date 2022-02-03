{
    "name": "Marketplace Automation: Payment Fees",
    "version": "1.1",
    'sequence': 1000,
    "category": "Extra",
    "summary": "Allow users to post payment gateway's fees to the configured account in workflow.",

    "depends": ['marketplace_automation_ts', 'shopify'],

    'data': [
        'views/order_workflow_view.xml',
    ],

    'images': ['static/description/slideshow/mk_automation.gif'],

    "author": "Teqstars",
    "website": "https://teqstars.com",
    'support': 'support@teqstars.com',
    'maintainer': 'Teqstars',

    "description": """""",

    'demo': [],
    'license': 'OPL-1',
    'auto_install': False,
    'installable': True,
    'application': False,
}
