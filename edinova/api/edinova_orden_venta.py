import requests
from .credential import get_config

def get_ordenes_venta(fecha_inicio):
    try:
        config = get_config()

        nit_filtro = config["nit"]
        url = config["url"]
        token = config["token"]
        fecha = fecha_inicio
        # Parámetros de la solicitud
        headers = {
            "Authorization": f"Bearer {token}"
        }
        params = {
            "Fecha": fecha  # Formato: YYYY-MM-DD
        }

        # Petición a la API
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()

        if not data.get("success"):
            print("La API respondió con éxito=False")
            return []

        ordenes_filtradas = []

        for grupo in data.get("data", []):
            for orden in grupo.get("purchaseOrders", []):
                supplier = orden.get("supplier", {})
                if supplier.get("nit") == nit_filtro:
                    ordenes_filtradas.append(orden)

        return ordenes_filtradas

    except requests.exceptions.RequestException as e:
        print(f"Error en la solicitud: {e}")
        return []

    except Exception as e:
        print(f"Error inesperado: {e}")
        return []
