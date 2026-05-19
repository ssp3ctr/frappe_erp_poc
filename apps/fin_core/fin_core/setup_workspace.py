import frappe


def run():
    """Create Accounting workspace with receipt-workspace shortcut."""
    if frappe.db.exists("Workspace", "Accounting"):
        print("Workspace 'Accounting' already exists")
        return

    ws = frappe.get_doc({
        "doctype": "Workspace",
        "name": "Accounting",
        "label": "Accounting",
        "title": "Accounting",
        "module": "Accounting",
        "public": 1,
        "icon": "accounting",
        "indicator_color": "blue",
        "content": "[]",
    })
    ws.append("shortcuts", {
        "label": "Оприбуткування",
        "type": "Page",
        "link_to": "receipt-workspace",
        "icon": "folder",
    })
    ws.append("links", {
        "label": "Документи",
        "type": "Card Break",
        "link_to": None,
        "onboard": 0,
    })
    ws.append("links", {
        "label": "Receipt",
        "type": "Link",
        "link_to": "Receipt",
        "link_type": "DocType",
        "onboard": 1,
    })
    ws.flags.ignore_links = True
    ws.insert(ignore_permissions=True)
    frappe.db.commit()
    print("Created Accounting workspace with receipt-workspace shortcut")
