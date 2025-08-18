import frappe
from frappe import _

def get_config():
    credentials = frappe.get_doc("Credential", "Credential")
    return {
        "nit": credentials.nit,
        "url": credentials.url,
        "token": credentials.token
    }