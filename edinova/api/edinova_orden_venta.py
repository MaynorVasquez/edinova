import frappe
import requests

from .credential import get_config

logger = frappe.logger("edinova", allow_site=True, file_count=10)


def get_ordenes_venta(fecha_inicio: str) -> list[dict]:
    """Consulta la API de Walmart y devuelve sólo las órdenes cuyo proveedor coincide con el NIT configurado."""
    try:
        config = get_config()
        nit_filtro = config["nit"]
        url = config["url"]
        token = config["token"]

        headers = {"Authorization": f"Bearer {token}"}
        params = {"Fecha": fecha_inicio}

        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()

        if not data.get("success"):
            logger.warning("La API respondió con success=False")
            return []

        ordenes_filtradas = []
        for grupo in data.get("data", []):
            for orden in grupo.get("purchaseOrders", []):
                supplier = orden.get("supplier", {})
                if supplier.get("nit") == nit_filtro:
                    ordenes_filtradas.append(orden)

        return ordenes_filtradas

    except requests.exceptions.RequestException:
        frappe.log_error(frappe.get_traceback(), "Edinova API request")
        logger.exception("Error en la solicitud HTTP")
        return []

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Edinova get_ordenes_venta")
        logger.exception("Error inesperado")
        return []
