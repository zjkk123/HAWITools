{
    "name": "Odoo Shopify Connector",
    "version": "1.3",
    "category": "Sales",
    'summary': 'Integrate & Manage Shopify Operations from Odoo by using Shopify Integration. We also provide modules like shipping dhl express fedex ups gls usps stamps.com shipstation easyship amazon sendclound ebay woocommerce bol.com catch',

    "depends": ['marketplace_automation_ts', 'base_marketplace'],

    'data': [
        'security/ir.model.access.csv',

        'views/marketplace_listing_item_view.xml',
        'views/marketplace_listing_view.xml',
        'views/collection_view.xml',

        'views/location_view.xml',
        'views/shopify_tags_view.xml',
        'views/shopify_image_view.xml',
        'views/delivery_carrier_view.xml',
        'views/shopify_payment_gateway_view.xml',
        'views/sale_order_view.xml',
        'views/stock_view.xml',

        'wizards/cancel_order_in_marketplace_view.xml',
        'wizards/operation_view.xml',

        'views/marketplace_instance_view.xml',
        'views/marketplace_listing_image_view.xml',
        # 'views/payout_view.xml',
        'views/shopify_menuitem.xml',

        'data/fulfillment_status_data.xml',
        'data/ir_sequence_data.xml',
        'data/data.xml',

    ],

    'images': ['static/description/shopify_banner.png'],

    'assets': {
        'web.assets_backend': [
            '/shopify/static/src/scss/shopify_dashboard.scss',
        ],
    },

    "author": "Teqstars",
    "website": "https://teqstars.com",
    'support': 'support@teqstars.com',
    'maintainer': 'Teqstars',

    "description": """
        """,

    'demo': [],
    'license': 'OPL-1',
    'live_test_url': 'https://youtu.be/DR9f_sSZzx0',
    'auto_install': False,
    'installable': True,
    'application': True,
    'qweb': [],
    "price": "199.99",
    "currency": "EUR",
}
