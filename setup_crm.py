import os, json

os.makedirs("css", exist_ok=True)
os.makedirs("js", exist_ok=True)
os.makedirs("data", exist_ok=True)

files = {}

files["requirements.txt"] = """flask==3.0.3
pymongo==4.8.0
"""

files["app.py"] = """import os
import json
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from pymongo import MongoClient
from bson.objectid import ObjectId

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP_FILE = os.path.join(BASE_DIR, "data", "leads_backup.json")

app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")

# Conexión a MongoDB local con respaldo automático a JSON si no hay servicio Mongo activo
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
use_mongo = False
collection = None

try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=1500)
    client.admin.command("ping")
    db = client["premxca_crm"]
    collection = db["leads"]
    use_mongo = True
except Exception as e:
    print(f"[CRM] MongoDB no disponible ({e}). Usando respaldo local JSON en data/leads_backup.json")

STAGES = [
    "Cotización Web",
    "Promo 15% Enviada",
    "En Negociación",
    "Apartado",
    "Boleto Confirmado"
]

TICKET_PRICE = 120000
SECOND_TICKET_DISCOUNT = 0.15


def load_json_leads():
    if not os.path.exists(BACKUP_FILE):
        return []
    with open(BACKUP_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def save_json_leads(leads):
    with open(BACKUP_FILE, "w", encoding="utf-8") as f:
        json.dump(leads, f, ensure_ascii=False, indent=2)


def calculate_quote(tickets: int):
    tickets = max(1, int(tickets or 1))
    subtotal = tickets * TICKET_PRICE
    discount_amount = 0
    promo_applied = False

    if tickets >= 2:
        promo_applied = True
        # 15% de descuento en el 2º boleto (o pares adicionales)
        discounted_pairs = tickets // 2
        discount_amount = int(round(discounted_pairs * TICKET_PRICE * SECOND_TICKET_DISCOUNT))

    total = subtotal - discount_amount
    return {
        "tickets": tickets,
        "unit_price": TICKET_PRICE,
        "subtotal": subtotal,
        "promo_second_ticket_15": promo_applied,
        "discount_amount": discount_amount,
        "total": total
    }


def serialize_doc(doc):
    item = dict(doc)
    if "_id" in item:
        item["id"] = str(item.pop("_id"))
    return item


@app.route("/")
def serve_index():
    return send_from_directory(os.path.join(BASE_DIR, "oaxaca-travel-web"), "index.html")


@app.route("/crm")
def serve_crm():
    return send_from_directory(BASE_DIR, "crm.html")


@app.route("/<path:filename>")
def serve_static(filename):
    full_path = os.path.join(BASE_DIR, filename)
    if os.path.exists(full_path) and os.path.isfile(full_path):
        return send_from_directory(BASE_DIR, filename)
    return jsonify({"error": "Archivo no encontrado"}), 404


@app.route("/api/leads", methods=["GET"])
def get_leads():
    stage_filter = request.args.get("stage", "").strip()
    departure_filter = request.args.get("departure", "").strip()
    search = request.args.get("q", "").strip().lower()

    if use_mongo:
        docs = [serialize_doc(d) for d in collection.find().sort("created_at", -1)]
    else:
        docs = list(reversed(load_json_leads()))

    filtered = []
    for lead in docs:
        if stage_filter and stage_filter != "ALL" and lead.get("stage") != stage_filter:
            continue
        if departure_filter and departure_filter != "ALL" and lead.get("departure") != departure_filter:
            continue
        if search:
            haystack = " ".join([
                str(lead.get("name", "")),
                str(lead.get("email", "")),
                str(lead.get("phone", "")),
                str(lead.get("departure", ""))
            ]).lower()
            if search not in haystack:
                continue
        filtered.append(lead)

    return jsonify({"leads": filtered, "storage": "mongodb" if use_mongo else "json_backup"})


@app.route("/api/leads", methods=["POST"])
def create_lead():
    data = request.get_json(force=True, silent=True) or {}

    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    departure = (data.get("departure") or "Salida 1 • Primavera").strip()
    notes_initial = (data.get("notes") or "").strip()

    try:
        tickets = int(data.get("tickets", 1))
    except (ValueError, TypeError):
        tickets = 1

    if not name or (not email and not phone):
        return jsonify({"error": "Nombre y al menos un medio de contacto (correo o teléfono) son obligatorios."}), 400

    pricing = calculate_quote(tickets)
    now_iso = datetime.now().isoformat(timespec="seconds")

    note_history = []
    if notes_initial:
        note_history.append({"text": notes_initial, "created_at": now_iso})

    lead_doc = {
        "name": name,
        "email": email,
        "phone": phone,
        "route": "La Mixteca → Huazolotitlán → Puerto Ángel",
        "departure": departure,
        "tickets": pricing["tickets"],
        "unit_price": pricing["unit_price"],
        "subtotal": pricing["subtotal"],
        "promo_second_ticket_15": pricing["promo_second_ticket_15"],
        "discount_amount": pricing["discount_amount"],
        "total": pricing["total"],
        "stage": "Cotización Web",
        "notes_log": note_history,
        "created_at": now_iso,
        "updated_at": now_iso
    }

    if use_mongo:
        res = collection.insert_one(lead_doc)
        lead_doc["id"] = str(res.inserted_id)
        lead_doc.pop("_id", None)
    else:
        lead_doc["id"] = f"local-{int(datetime.now().timestamp() * 1000)}"
        leads = load_json_leads()
        leads.append(lead_doc)
        save_json_leads(leads)

    return jsonify({"message": "Cotización registrada en CRM", "lead": lead_doc}), 201


@app.route("/api/leads/<lead_id>", methods=["PATCH"])
def update_lead(lead_id):
    data = request.get_json(force=True, silent=True) or {}
    now_iso = datetime.now().isoformat(timespec="seconds")
    updates = {"updated_at": now_iso}

    if "stage" in data and data["stage"] in STAGES:
        updates["stage"] = data["stage"]

    if "departure" in data and str(data["departure"]).strip():
        updates["departure"] = str(data["departure"]).strip()

    if "tickets" in data:
        pricing = calculate_quote(data["tickets"])
        updates.update(pricing)

    new_note = (data.get("new_note") or "").strip()

    if use_mongo:
        try:
            oid = ObjectId(lead_id)
        except Exception:
            return jsonify({"error": "ID inválido"}), 400

        mongo_ops = {"$set": updates}
        if new_note:
            mongo_ops["$push"] = {"notes_log": {"text": new_note, "created_at": now_iso}}

        updated = collection.find_one_and_update({"_id": oid}, mongo_ops, return_document=True)
        if not updated:
            return jsonify({"error": "Lead no encontrado"}), 404
        return jsonify({"lead": serialize_doc(updated)})
    else:
        leads = load_json_leads()
        for lead in leads:
            if str(lead.get("id")) == str(lead_id):
                lead.update(updates)
                if new_note:
                    lead.setdefault("notes_log", []).append({"text": new_note, "created_at": now_iso})
                save_json_leads(leads)
                return jsonify({"lead": lead})
        return jsonify({"error": "Lead no encontrado"}), 404


@app.route("/api/leads/<lead_id>", methods=["DELETE"])
def delete_lead(lead_id):
    if use_mongo:
        try:
            oid = ObjectId(lead_id)
        except Exception:
            return jsonify({"error": "ID inválido"}), 400
        res = collection.delete_one({"_id": oid})
        if res.deleted_count == 0:
            return jsonify({"error": "Lead no encontrado"}), 404
        return jsonify({"deleted": True, "id": lead_id})
    else:
        leads = load_json_leads()
        new_leads = [l for l in leads if str(l.get("id")) != str(lead_id)]
        if len(new_leads) == len(leads):
            return jsonify({"error": "Lead no encontrado"}), 404
        save_json_leads(new_leads)
        return jsonify({"deleted": True, "id": lead_id})


@app.route("/api/stats", methods=["GET"])
def get_stats():
    if use_mongo:
        leads = [serialize_doc(d) for d in collection.find()]
    else:
        leads = load_json_leads()

    total_leads = len(leads)
    pipeline_amount = sum(int(l.get("total", 0)) for l in leads if l.get("stage") != "Boleto Confirmado")
    confirmed_leads = [l for l in leads if l.get("stage") == "Boleto Confirmado"]
    confirmed_amount = sum(int(l.get("total", 0)) for l in confirmed_leads)
    confirmed_tickets = sum(int(l.get("tickets", 1)) for l in confirmed_leads)
    promo_count = sum(1 for l in leads if l.get("promo_second_ticket_15"))

    conversion_rate = round((len(confirmed_leads) / total_leads) * 100, 1) if total_leads > 0 else 0.0

    by_stage = {s: 0 for s in STAGES}
    for l in leads:
        st = l.get("stage", "Cotización Web")
        by_stage[st] = by_stage.get(st, 0) + 1

    return jsonify({
        "total_leads": total_leads,
        "pipeline_amount": pipeline_amount,
        "confirmed_leads": len(confirmed_leads),
        "confirmed_amount": confirmed_amount,
        "confirmed_tickets": confirmed_tickets,
        "annual_departures_cap": 5,
        "promo_count": promo_count,
        "conversion_rate": conversion_rate,
        "by_stage": by_stage,
        "storage": "mongodb" if use_mongo else "json_backup"
    })


if __name__ == "__main__":
    print("PREMXCA CRM corriendo en http://127.0.0.1:5000")
    print("Web pública: http://127.0.0.1:5000/")
    print("Panel CRM:   http://127.0.0.1:5000/crm")
    app.run(host="127.0.0.1", port=5000, debug=True)
"""

files["crm.html"] = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>PREMXCA | Panel CRM Ejecutivo</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@600;700&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet" />
  <script src="https://unpkg.com/@phosphor-icons/web"></script>
  <link rel="stylesheet" href="css/crm-dashboard.css" />
</head>
<body>
  <div class="crm-bg-glow"></div>

  <header class="crm-topbar">
    <div class="brand-group">
      <img src="logoazul.jpeg" alt="PREMXCA Logo" class="brand-logo" onerror="this.style.display='none'" />
      <div>
        <span class="brand-kicker">CONCIERGE &amp; VENTAS • LOCALHOST</span>
        <h1 class="brand-title">PREMXCA CRM</h1>
      </div>
    </div>

    <div class="topbar-actions">
      <span id="storageBadge" class="storage-pill">Conectando...</span>
      <a href="/" class="btn-ghost"><i class="ph ph-arrow-square-out"></i> Ver Web Pública</a>
      <button id="btnSeedDemo" class="btn-accent"><i class="ph ph-sparkle"></i> Cargar Leads Demo</button>
    </div>
  </header>

  <main class="crm-container">
    <!-- KPIs -->
    <section class="kpi-grid" id="kpiGrid">
      <article class="kpi-card glass-card">
        <div class="kpi-head">
          <span>Pipeline Abierto</span>
          <i class="ph ph-currency-dollar"></i>
        </div>
        <strong id="kpiPipeline" class="kpi-value">$0 MXN</strong>
        <small id="kpiTotalLeads">0 cotizaciones activas</small>
      </article>

      <article class="kpi-card glass-card">
        <div class="kpi-head">
          <span>Boletos Confirmados</span>
          <i class="ph ph-check-circle"></i>
        </div>
        <strong id="kpiConfirmedAmount" class="kpi-value">$0 MXN</strong>
        <small id="kpiConfirmedTickets">0 boletos vendidos • 5 salidas/año</small>
      </article>

      <article class="kpi-card glass-card">
        <div class="kpi-head">
          <span>Promo 15% (2º Boleto)</span>
          <i class="ph ph-tag"></i>
        </div>
        <strong id="kpiPromoCount" class="kpi-value">0</strong>
        <small>Cotizaciones de 2+ boletos</small>
      </article>

      <article class="kpi-card glass-card">
        <div class="kpi-head">
          <span>Tasa de Conversión</span>
          <i class="ph ph-trend-up"></i>
        </div>
        <strong id="kpiConversion" class="kpi-value">0%</strong>
        <small>Ruta: Mixteca → Huazolotitlán → Puerto Ángel</small>
      </article>
    </section>

    <!-- Filtros -->
    <section class="toolbar glass-card">
      <div class="search-box">
        <i class="ph ph-magnifying-glass"></i>
        <input type="text" id="searchInput" placeholder="Buscar prospecto por nombre, correo o teléfono..." />
      </div>

      <div class="filter-group">
        <select id="departureFilter">
          <option value="ALL">Todas las salidas (5 al año)</option>
          <option value="Salida 1 • Primavera">Salida 1 • Primavera</option>
          <option value="Salida 2 • Verano">Salida 2 • Verano</option>
          <option value="Salida 3 • Otoño">Salida 3 • Otoño</option>
          <option value="Salida 4 • Invierno">Salida 4 • Invierno</option>
          <option value="Salida 5 • Edición Especial">Salida 5 • Edición Especial</option>
        </select>

        <button id="btnRefresh" class="btn-ghost"><i class="ph ph-arrows-clockwise"></i> Actualizar</button>
      </div>
    </section>

    <!-- Tablero Kanban -->
    <section class="kanban-board" id="kanbanBoard"></section>
  </main>

  <!-- Drawer de detalle y seguimiento -->
  <aside class="drawer-backdrop" id="drawerBackdrop" aria-hidden="true">
    <div class="drawer-panel glass-card">
      <div class="drawer-header">
        <div>
          <span class="drawer-kicker">EXPEDIENTE DE PROSPECTO</span>
          <h2 id="drawerName">Nombre</h2>
        </div>
        <button id="btnCloseDrawer" class="icon-btn" aria-label="Cerrar"><i class="ph ph-x"></i></button>
      </div>

      <div id="drawerBody" class="drawer-body"></div>

      <div class="drawer-footer">
        <button id="btnDeleteLead" class="btn-danger"><i class="ph ph-trash"></i> Eliminar Lead</button>
        <button id="btnSaveLead" class="btn-accent"><i class="ph ph-floppy-disk"></i> Guardar Cambios</button>
      </div>
    </div>
  </aside>

  <script src="js/crm-dashboard.js"></script>
</body>
</html>
"""

files["css/crm-dashboard.css"] = """:root {
  --navy-deep: #061224;
  --navy-primary: #0b1f3a;
  --navy-surface: rgba(16, 37, 68, 0.72);
  --navy-border: rgba(255, 255, 255, 0.14);
  --white-pure: #ffffff;
  --ivory-soft: #f5f3ef;
  --muted-text: rgba(255, 255, 255, 0.68);
  --rosa-mexicano: #e6007e;
  --rosa-hover: #ff1a94;
  --font-serif: "Playfair Display", Georgia, serif;
  --font-sans: "Plus Jakarta Sans", system-ui, -apple-system, sans-serif;
}

* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  font-family: var(--font-sans);
  background: radial-gradient(circle at 15% 10%, #112d54 0%, var(--navy-deep) 55%, #030914 100%);
  color: var(--white-pure);
  min-height: 100vh;
  overflow-x: hidden;
}

.crm-bg-glow {
  position: fixed;
  width: 440px;
  height: 440px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(230, 0, 126, 0.18), transparent 70%);
  top: -120px;
  right: -120px;
  pointer-events: none;
  z-index: 0;
}

.glass-card {
  background: var(--navy-surface);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border: 1px solid var(--navy-border);
  border-radius: 18px;
}

.crm-topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 16px 28px;
  background: rgba(6, 18, 36, 0.85);
  backdrop-filter: blur(16px);
  border-bottom: 1px solid var(--navy-border);
}

.brand-group {
  display: flex;
  align-items: center;
  gap: 14px;
}

.brand-logo {
  width: 46px;
  height: 46px;
  border-radius: 50%;
  object-fit: cover;
  border: 1.5px solid rgba(255, 255, 255, 0.35);
}

.brand-kicker {
  font-size: 11px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--rosa-mexicano);
  font-weight: 700;
}

.brand-title {
  font-family: var(--font-serif);
  font-size: 22px;
  font-weight: 700;
  color: var(--white-pure);
}

.topbar-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.storage-pill {
  font-size: 12px;
  padding: 6px 12px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid var(--navy-border);
  color: var(--ivory-soft);
}

.btn-ghost, .btn-accent, .btn-danger, .icon-btn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 9px 16px;
  border-radius: 999px;
  font-family: var(--font-sans);
  font-size: 13px;
  font-weight: 600;
  text-decoration: none;
  cursor: pointer;
  transition: all 0.2s ease;
  border: 1px solid transparent;
}

.btn-ghost {
  background: rgba(255, 255, 255, 0.06);
  color: var(--white-pure);
  border-color: var(--navy-border);
}

.btn-ghost:hover {
  border-color: var(--rosa-mexicano);
  color: var(--rosa-hover);
}

.btn-accent {
  background: var(--rosa-mexicano);
  color: var(--white-pure);
}

.btn-accent:hover {
  background: var(--rosa-hover);
  transform: translateY(-1px);
}

.btn-danger {
  background: rgba(239, 68, 68, 0.16);
  color: #fca5a5;
  border-color: rgba(239, 68, 68, 0.4);
}

.btn-danger:hover {
  background: rgba(239, 68, 68, 0.3);
}

.icon-btn {
  padding: 8px;
  background: rgba(255, 255, 255, 0.06);
  color: var(--white-pure);
  border-color: var(--navy-border);
}

.crm-container {
  position: relative;
  z-index: 1;
  max-width: 1440px;
  margin: 0 auto;
  padding: 24px 28px 60px;
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 16px;
  margin-bottom: 20px;
}

.kpi-card {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.kpi-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  color: var(--muted-text);
}

.kpi-head i {
  font-size: 20px;
  color: var(--rosa-mexicano);
}

.kpi-value {
  font-size: 26px;
  font-weight: 700;
  color: var(--white-pure);
}

.kpi-card small {
  font-size: 12px;
  color: var(--muted-text);
}

.toolbar {
  padding: 14px 18px;
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 14px;
  margin-bottom: 22px;
}

.search-box {
  display: flex;
  align-items: center;
  gap: 10px;
  background: rgba(6, 18, 36, 0.65);
  border: 1px solid var(--navy-border);
  border-radius: 999px;
  padding: 8px 16px;
  flex: 1;
  min-width: 240px;
}

.search-box i {
  color: var(--muted-text);
}

.search-box input {
  background: transparent;
  border: none;
  outline: none;
  color: var(--white-pure);
  width: 100%;
  font-size: 14px;
}

.filter-group {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

select, input, textarea {
  background: rgba(6, 18, 36, 0.75);
  color: var(--white-pure);
  border: 1px solid var(--navy-border);
  border-radius: 12px;
  padding: 9px 14px;
  font-family: var(--font-sans);
  font-size: 13px;
}

select:focus, input:focus, textarea:focus {
  outline: none;
  border-color: var(--rosa-mexicano);
}

.kanban-board {
  display: grid;
  grid-template-columns: repeat(5, minmax(265px, 1fr));
  gap: 16px;
  align-items: start;
  overflow-x: auto;
  padding-bottom: 12px;
}

.kanban-col {
  background: rgba(11, 31, 58, 0.55);
  border: 1px solid var(--navy-border);
  border-radius: 16px;
  padding: 14px;
  min-height: 420px;
}

.kanban-col.drag-over {
  border-color: var(--rosa-mexicano);
  background: rgba(230, 0, 126, 0.08);
}

.col-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--navy-border);
}

.col-title {
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.03em;
}

.col-count {
  font-size: 12px;
  padding: 2px 9px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.1);
}

.lead-card {
  background: rgba(6, 18, 36, 0.85);
  border: 1px solid var(--navy-border);
  border-radius: 14px;
  padding: 14px;
  margin-bottom: 10px;
  cursor: pointer;
  transition: transform 0.18s ease, border-color 0.18s ease;
}

.lead-card:hover {
  transform: translateY(-2px);
  border-color: rgba(230, 0, 126, 0.6);
}

.lead-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 6px;
}

.lead-name {
  font-weight: 700;
  font-size: 14px;
}

.lead-total {
  font-weight: 700;
  font-size: 13px;
  color: var(--ivory-soft);
  white-space: nowrap;
}

.lead-meta {
  font-size: 12px;
  color: var(--muted-text);
  margin-bottom: 8px;
}

.badges-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tag-promo {
  font-size: 11px;
  font-weight: 700;
  padding: 3px 8px;
  border-radius: 999px;
  background: rgba(230, 0, 126, 0.2);
  color: #ff80c4;
  border: 1px solid rgba(230, 0, 126, 0.45);
}

.tag-departure {
  font-size: 11px;
  padding: 3px 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.08);
  color: var(--ivory-soft);
}

/* Drawer */
.drawer-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(2, 8, 18, 0.72);
  backdrop-filter: blur(4px);
  display: none;
  justify-content: flex-end;
  z-index: 50;
}

.drawer-backdrop.open {
  display: flex;
}

.drawer-panel {
  width: 100%;
  max-width: 460px;
  height: 100%;
  border-radius: 20px 0 0 20px;
  display: flex;
  flex-direction: column;
  padding: 24px;
  overflow-y: auto;
}

.drawer-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--navy-border);
}

.drawer-kicker {
  font-size: 11px;
  letter-spacing: 0.12em;
  color: var(--rosa-mexicano);
  font-weight: 700;
}

.drawer-body {
  flex: 1;
  padding: 18px 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.field-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-group label {
  font-size: 12px;
  color: var(--muted-text);
}

.summary-box {
  background: rgba(6, 18, 36, 0.65);
  border: 1px solid var(--navy-border);
  border-radius: 12px;
  padding: 12px 14px;
  font-size: 13px;
  line-height: 1.55;
}

.notes-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 180px;
  overflow-y: auto;
}

.note-item {
  background: rgba(255, 255, 255, 0.05);
  border-left: 3px solid var(--rosa-mexicano);
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 12px;
}

.note-item small {
  display: block;
  color: var(--muted-text);
  margin-top: 4px;
  font-size: 11px;
}

.drawer-footer {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding-top: 14px;
  border-top: 1px solid var(--navy-border);
}
"""

files["js/crm-dashboard.js"] = """const STAGES = [
  "Cotización Web",
  "Promo 15% Enviada",
  "En Negociación",
  "Apartado",
  "Boleto Confirmado"
];

let currentLeads = [];
let activeLead = null;

const fmtMXN = (n) =>
  new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN", maximumFractionDigits: 0 }).format(Number(n || 0));

async function fetchStats() {
  const res = await fetch("/api/stats");
  const s = await res.json();

  document.getElementById("kpiPipeline").textContent = `${fmtMXN(s.pipeline_amount)} MXN`;
  document.getElementById("kpiTotalLeads").textContent = `${s.total_leads} cotizaciones registradas`;
  document.getElementById("kpiConfirmedAmount").textContent = `${fmtMXN(s.confirmed_amount)} MXN`;
  document.getElementById("kpiConfirmedTickets").textContent = `${s.confirmed_tickets} boletos • ${s.annual_departures_cap} salidas/año`;
  document.getElementById("kpiPromoCount").textContent = s.promo_count;
  document.getElementById("kpiConversion").textContent = `${s.conversion_rate}%`;
  document.getElementById("storageBadge").textContent =
    s.storage === "mongodb" ? "MongoDB Activo" : "Respaldo JSON Local";
}

async function fetchLeads() {
  const q = encodeURIComponent(document.getElementById("searchInput").value.trim());
  const dep = encodeURIComponent(document.getElementById("departureFilter").value);
  const res = await fetch(`/api/leads?q=${q}&departure=${dep}`);
  const data = await res.json();
  currentLeads = data.leads || [];
  renderKanban();
}

function renderKanban() {
  const board = document.getElementById("kanbanBoard");
  board.innerHTML = "";

  STAGES.forEach((stage) => {
    const colLeads = currentLeads.filter((l) => (l.stage || "Cotización Web") === stage);
    const col = document.createElement("div");
    col.className = "kanban-col";
    col.dataset.stage = stage;

    col.addEventListener("dragover", (e) => {
      e.preventDefault();
      col.classList.add("drag-over");
    });
    col.addEventListener("dragleave", () => col.classList.remove("drag-over"));
    col.addEventListener("drop", async (e) => {
      e.preventDefault();
      col.classList.remove("drag-over");
      const leadId = e.dataTransfer.getData("text/plain");
      if (leadId) {
        await patchLead(leadId, { stage });
        await refreshAll();
      }
    });

    col.innerHTML = `
      <div class="col-header">
        <span class="col-title">${stage}</span>
        <span class="col-count">${colLeads.length}</span>
      </div>
    `;

    colLeads.forEach((lead) => {
      const card = document.createElement("div");
      card.className = "lead-card";
      card.draggable = true;

      card.addEventListener("dragstart", (e) => {
        e.dataTransfer.setData("text/plain", lead.id);
      });
      card.addEventListener("click", () => openDrawer(lead));

      const promoBadge = lead.promo_second_ticket_15
        ? `<span class="tag-promo">-15% 2º Boleto</span>`
        : "";

      card.innerHTML = `
        <div class="lead-top">
          <span class="lead-name">${escapeHtml(lead.name)}</span>
          <span class="lead-total">${fmtMXN(lead.total)}</span>
        </div>
        <div class="lead-meta">
          ${lead.tickets || 1} boleto(s) • ${escapeHtml(lead.phone || lead.email || "")}
        </div>
        <div class="badges-row">
          <span class="tag-departure">${escapeHtml(lead.departure || "Salida 1")}</span>
          ${promoBadge}
        </div>
      `;
      col.appendChild(card);
    });

    board.appendChild(col);
  });
}

function openDrawer(lead) {
  activeLead = lead;
  document.getElementById("drawerName").textContent = lead.name;

  const notesHtml = (lead.notes_log || []).length
    ? lead.notes_log.map((n) => `<div class="note-item">${escapeHtml(n.text)}<small>${escapeHtml(n.created_at || "")}</small></div>`).join("")
    : `<div class="note-item">Sin notas registradas todavía.</div>`;

  document.getElementById("drawerBody").innerHTML = `
    <div class="summary-box">
      <div><strong>Ruta:</strong> ${escapeHtml(lead.route || "La Mixteca → Huazolotitlán → Puerto Ángel")}</div>
      <div><strong>Contacto:</strong> ${escapeHtml(lead.email || "—")} • ${escapeHtml(lead.phone || "—")}</div>
      <div><strong>Boletos:</strong> ${lead.tickets} | <strong>Subtotal:</strong> ${fmtMXN(lead.subtotal)}</div>
      <div><strong>Descuento 15% (2º boleto):</strong> -${fmtMXN(lead.discount_amount)}</div>
      <div><strong>Total Cotizado:</strong> <strong>${fmtMXN(lead.total)} MXN</strong></div>
    </div>

    <div class="field-group">
      <label>Etapa del Embudo</label>
      <select id="drawerStage">
        ${STAGES.map((s) => `<option value="${s}" ${lead.stage === s ? "selected" : ""}>${s}</option>`).join("")}
      </select>
    </div>

    <div class="field-group">
      <label>Cantidad de Boletos ($120,000 MXN c/u • 15% en 2º)</label>
      <input type="number" id="drawerTickets" min="1" max="20" value="${lead.tickets || 1}" />
    </div>

    <div class="field-group">
      <label>Agregar Nota de Seguimiento</label>
      <textarea id="drawerNewNote" rows="2" placeholder="Ej. Llamó interesado en hospedaje y taller artesanal..."></textarea>
    </div>

    <div class="field-group">
      <label>Bitácora</label>
      <div class="notes-list">${notesHtml}</div>
    </div>
  `;

  document.getElementById("drawerBackdrop").classList.add("open");
}

function closeDrawer() {
  activeLead = null;
  document.getElementById("drawerBackdrop").classList.remove("open");
}

async function patchLead(id, payload) {
  await fetch(`/api/leads/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
}

async function seedDemoLeads() {
  const demos = [
    { name: "Valeria Mendoza", email: "valeria.m@ejemplo.mx", phone: "55 4120 8891", tickets: 2, departure: "Salida 1 • Primavera", notes: "Viaja en pareja, quiere detalles de Puerto Ángel." },
    { name: "Ricardo Salgado", email: "rsalgado@ejemplo.mx", phone: "55 3901 4412", tickets: 1, departure: "Salida 2 • Verano", notes: "Solicitó itinerario privado en La Mixteca." },
    { name: "Fernanda Ochoa", email: "fer.ochoa@ejemplo.mx", phone: "33 2188 7760", tickets: 2, departure: "Salida 3 • Otoño", notes: "Aplicó promo 15% segundo boleto." }
  ];
  for (const d of demos) {
    await fetch("/api/leads", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(d)
    });
  }
  await refreshAll();
}

function escapeHtml(str) {
  return String(str || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function refreshAll() {
  await Promise.all([fetchStats(), fetchLeads()]);
}

document.getElementById("btnRefresh").addEventListener("click", refreshAll);
document.getElementById("btnSeedDemo").addEventListener("click", seedDemoLeads);
document.getElementById("searchInput").addEventListener("input", fetchLeads);
document.getElementById("departureFilter").addEventListener("change", fetchLeads);
document.getElementById("btnCloseDrawer").addEventListener("click", closeDrawer);

document.getElementById("btnSaveLead").addEventListener("click", async () => {
  if (!activeLead) return;
  const stage = document.getElementById("drawerStage").value;
  const tickets = parseInt(document.getElementById("drawerTickets").value, 10) || 1;
  const new_note = document.getElementById("drawerNewNote").value.trim();
  await patchLead(activeLead.id, { stage, tickets, new_note });
  closeDrawer();
  await refreshAll();
});

document.getElementById("btnDeleteLead").addEventListener("click", async () => {
  if (!activeLead) return;
  await fetch(`/api/leads/${encodeURIComponent(activeLead.id)}`, { method: "DELETE" });
  closeDrawer();
  await refreshAll();
});

refreshAll();
"""

files["js/crm-lead.js"] = """// Conecta formularios de cotización en index.html con el backend local Flask (/api/leads)
(function () {
  async function submitQuoteToCRM(payload) {
    try {
      const res = await fetch("/api/leads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || "Error al enviar cotización al CRM");
      }
      return await res.json();
    } catch (err) {
      console.warn("CRM local no alcanzado en este entorno:", err.message);
      return null;
    }
  }

  // Intercepta automáticamente cualquier formulario que tenga data-crm-quote o id="quoteForm" / "leadForm"
  document.addEventListener("submit", async (e) => {
    const form = e.target.closest("form[data-crm-quote], #quoteForm, #leadForm");
    if (!form) return;

    e.preventDefault();
    const fd = new FormData(form);
    const data = Object.fromEntries(fd.entries());

    const payload = {
      name: data.name || data.nombre || form.querySelector('input[name*="name"], input[name*="nombre"]')?.value || "",
      email: data.email || data.correo || form.querySelector('input[type="email"]')?.value || "",
      phone: data.phone || data.telefono || form.querySelector('input[type="tel"]')?.value || "",
      tickets: parseInt(data.tickets || data.boletos || form.querySelector('input[type="number"], select[name*="ticket"], select[name*="boleto"]')?.value || "1", 10),
      departure: data.departure || data.salida || form.querySelector('select[name*="departure"], select[name*="salida"]')?.value || "Salida 1 • Primavera",
      notes: data.notes || data.notas || form.querySelector("textarea")?.value || ""
    };

    const result = await submitQuoteToCRM(payload);
    if (result && result.lead) {
      const totalFmt = new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN", maximumFractionDigits: 0 }).format(result.lead.total);
      alert(`Cotización registrada en PREMXCA CRM. Total estimado: ${totalFmt} MXN${result.lead.promo_second_ticket_15 ? " (incluye 15% en 2º boleto)" : ""}.`);
      form.reset();
    }
  });

  window.PremxcaCRM = { submitQuoteToCRM };
})();
"""

for path, content in files.items():
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Generado: {path}")
