{
    'name': "Update invoice report",
    'category': "report",
    'summary': 'Update Body and Header of invoice',
    'depends': ["base", "account_accountant", "sale_management"],
    'data': [
        'report/Header_Update.xml',
        'report/Body_Update.xml'
    ],
    'installable': True,
    'demo': [],
    'application':False,
    'auto_install': False
}
