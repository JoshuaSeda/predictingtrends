import json
import os
import uuid
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta, date
from collections import Counter

app = Flask(__name__)
app.secret_key = "local_test_key"

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
INVENTORY_FILE = os.path.join(DATA_DIR, "inventory.json")
SALES_FILE = os.path.join(DATA_DIR, "sales.json")
ARCHIVE_FILE = os.path.join(DATA_DIR, "sales_archive.json")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
USERNAME = "admin"
PASSWORD = "passw0rd"

# ── First Monday your app started tracking sales ──────────────────────
SALES_ANCHOR = date(2026, 3, 16)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def load_json(filepath):
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(filepath):
        with open(filepath, "w") as f:
            json.dump([], f, indent=4)
    with open(filepath, "r") as f:
        try:
            return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []

def save_json(filepath, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(filepath, "w") as f:
        json.dump(data, f, indent=4)

def get_current_week_monday():
    today = date.today()
    return today - timedelta(days=today.weekday())

def auto_archive_old_sales():
    monday = get_current_week_monday()
    all_sales = load_json(SALES_FILE)
    current_week = []
    to_archive = []
    for sale in all_sales:
        date_str = sale.get("datetime", "")[:10]
        try:
            sale_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            current_week.append(sale)
            continue
        if sale_date >= monday:
            current_week.append(sale)
        else:
            to_archive.append(sale)
    if to_archive:
        archived = load_json(ARCHIVE_FILE)
        archived.extend(to_archive)
        save_json(ARCHIVE_FILE, archived)
        save_json(SALES_FILE, current_week)
    return current_week, len(to_archive)

def get_category_from_sale(sale):
    category = sale.get("category", "")
    if category and category != "Other":
        return category
    p_name = ""
    product = sale.get("product", {})
    if isinstance(product, dict):
        p_name = product.get("name", "").lower()
    elif isinstance(product, str):
        p_name = product.lower()
    if "jacket" in p_name or "hoodie" in p_name:
        return "Jackets"
    elif "short" in p_name:
        return "Shorts"
    elif "shirt" in p_name or "jersey" in p_name:
        return "T-shirts"
    return "Other"

# ── Build the list of all Mondays from ANCHOR → current week ──────────
def get_all_weeks():
    """Return list of {start, end, label} dicts, newest first."""
    anchor_monday = SALES_ANCHOR - timedelta(days=SALES_ANCHOR.weekday())
    current_monday = get_current_week_monday()
    weeks = []
    ws = anchor_monday
    while ws <= current_monday:
        we = ws + timedelta(days=6)
        weeks.append({
            "start": ws.strftime("%Y-%m-%d"),
            "end":   we.strftime("%Y-%m-%d"),
            "label": f"{ws.strftime('%b %d')} – {we.strftime('%b %d, %Y')}"
        })
        ws += timedelta(days=7)
    return list(reversed(weeks))   # newest first


@app.route("/api/inventory")
def api_inventory():
    inventory = load_json(INVENTORY_FILE)
    active_inventory = []
    for item in inventory:
        total_qty = (int(item.get('stock_s', 0)) + int(item.get('stock_m', 0)) +
                     int(item.get('stock_l', 0)) + int(item.get('stock_xl', 0)))
        if total_qty > 0:
            active_inventory.append(item)
    return jsonify(active_inventory)

@app.route("/checkout", methods=["POST"])
def checkout():
    if "user" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    data = request.get_json()
    cart_items = data.get('items', [])
    payment_method = data.get('payment_method', 'Cash')
    buyer_name = data.get('customer_name', 'Walk-in Customer')
    amount_tendered = float(data.get('amount_tendered', 0))
    if not cart_items:
        return jsonify({"success": False, "message": "Cart is empty"}), 400
    inventory = load_json(INVENTORY_FILE)
    inventory_map = {str(item.get('id')): item for item in inventory if 'id' in item}
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sales_to_save = []
    for cart_item in cart_items:
        item_id_str = str(cart_item.get('id'))
        quantity = int(cart_item.get('quantity', 0))
        size_type = cart_item.get('size', 'S').lower()
        size_key = f"stock_{size_type}"
        if item_id_str in inventory_map:
            product = inventory_map[item_id_str]
            current_stock = int(product.get(size_key, 0))
            current_price = float(product.get("price", 0.0))
            if current_stock >= quantity:
                total_price = current_price * quantity
                display_name = f"{product['name']} ({size_type.upper()})"
                new_sale = {
                    "sale_id": str(uuid.uuid4()),
                    "datetime": now_str,
                    "product": {"name": display_name},
                    "product_id": product.get("id"),
                    "category": product.get("category", "Other"),
                    "quantity": quantity,
                    "unit_price": current_price,
                    "total_price": total_price,
                    "payment_method": payment_method,
                    "buyer_name": buyer_name,
                    "amount_tendered": amount_tendered
                }
                sales_to_save.append(new_sale)
                product[size_key] = current_stock - quantity
            else:
                return jsonify({"success": False, "message": f"Low stock for {product['name']} size {size_type.upper()}"}), 400
        else:
            return jsonify({"success": False, "message": f"Item ID {item_id_str} not found in inventory."}), 400
    if sales_to_save:
        all_sales = load_json(SALES_FILE)
        all_sales.extend(sales_to_save)
        save_json(SALES_FILE, all_sales)
        save_json(INVENTORY_FILE, inventory)
        return jsonify({"success": True, "message": "Sale recorded successfully!"})
    return jsonify({"success": False, "message": "Transaction failed: No items processed."}), 400

@app.route("/")
def home():
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form["username"] == USERNAME and request.form["password"] == PASSWORD:
            session["user"] = request.form["username"]
            return redirect(url_for("dashboard"))
        return render_template("login.html", error="Invalid credentials")
    return render_template("login.html")

@app.route("/dashboard")
def dashboard():
    if "user" not in session: return redirect(url_for("login"))
    inv = load_json(INVENTORY_FILE)
    sales_list = load_json(SALES_FILE)
    total_items = len(inv)
    units_sold = sum(int(s.get("quantity", 0)) for s in sales_list)
    sales_counts = Counter()
    for s in sales_list:
        name = s.get("product", {}).get("name", "Unknown")
        sales_counts[name] += int(s.get("quantity", 0))
    sold_items_list = [{"name": name, "quantity": qty} for name, qty in sales_counts.items()]
    low_stock_items = []
    for i in inv:
        if any(int(i.get(key, 0)) < 3 for key in ['stock_s', 'stock_m', 'stock_l', 'stock_xl']):
            low_stock_items.append(i)
    return render_template("dashboard.html",
                           total_items=total_items,
                           items=inv,
                           units_sold=units_sold,
                           sold_items_list=sold_items_list,
                           low_stock=len(low_stock_items),
                           low_stock_items=low_stock_items,
                           user=session["user"])

@app.route("/inventory", methods=["GET", "POST"])
def inventory():
    if "user" not in session: return redirect(url_for("login"))
    items = load_json(INVENTORY_FILE)
    if request.method == "POST":
        for item in items:
            oid = str(item.get("id"))
            if request.form.get(f"name_{oid}"):
                item["name"]     = request.form.get(f"name_{oid}", item["name"])
                item["category"] = request.form.get(f"category_{oid}", item.get("category","Other"))
                item["price"]    = float(request.form.get(f"price_{oid}", 0) or 0)
                item["stock_s"]  = int(request.form.get(f"stock_s_{oid}", 0) or 0)
                item["stock_m"]  = int(request.form.get(f"stock_m_{oid}", 0) or 0)
                item["stock_l"]  = int(request.form.get(f"stock_l_{oid}", 0) or 0)
                item["stock_xl"] = int(request.form.get(f"stock_xl_{oid}", 0) or 0)

        new_names      = request.form.getlist("new_name[]")
        new_categories = request.form.getlist("new_category[]")
        new_prices     = request.form.getlist("new_price[]")
        new_stock_s    = request.form.getlist("new_stock_s[]")
        new_stock_m    = request.form.getlist("new_stock_m[]")
        new_stock_l    = request.form.getlist("new_stock_l[]")
        new_stock_xl   = request.form.getlist("new_stock_xl[]")

        if new_names:
            existing_ids = [int(i["id"]) for i in items if "id" in i and str(i["id"]).isdigit()]
            max_id = max(existing_ids) if existing_ids else 0
            for i, n in enumerate(new_names):
                if n.strip():
                    max_id += 1
                    items.append({
                        "id":             max_id,
                        "name":           n.strip(),
                        "category":       new_categories[i] if i < len(new_categories) and new_categories[i] else "Other",
                        "price":          float(new_prices[i] or 0) if i < len(new_prices) and new_prices[i] else 0.0,
                        "stock_s":        int(new_stock_s[i] or 0) if i < len(new_stock_s) and new_stock_s[i] else 0,
                        "stock_m":        int(new_stock_m[i] or 0) if i < len(new_stock_m) and new_stock_m[i] else 0,
                        "stock_l":        int(new_stock_l[i] or 0) if i < len(new_stock_l) and new_stock_l[i] else 0,
                        "stock_xl":       int(new_stock_xl[i] or 0) if i < len(new_stock_xl) and new_stock_xl[i] else 0,
                        "image_filename": None
                    })

        save_json(INVENTORY_FILE, items)
        return redirect(url_for("inventory"))

    return render_template("inventory.html", items=items)

@app.route("/upload_image/<int:item_id>", methods=["POST"])
def upload_image(item_id):
    if "user" not in session: return redirect(url_for("login"))
    if "image" not in request.files: return redirect(url_for("inventory"))
    image = request.files["image"]
    if image and allowed_file(image.filename):
        filename = secure_filename(image.filename)
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
        name, ext = os.path.splitext(filename)
        filename = f"{name}_{int(datetime.now().timestamp())}{ext}"
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        image.save(filepath)
        items = load_json(INVENTORY_FILE)
        for item in items:
            if str(item.get("id")) == str(item_id):
                item["image_filename"] = filename
                break
        save_json(INVENTORY_FILE, items)
    return redirect(url_for("inventory"))

@app.route("/remove_item/<int:item_id>", methods=["POST"])
def remove_item(item_id):
    if "user" not in session: return redirect(url_for("login"))
    items = load_json(INVENTORY_FILE)
    updated = [item for item in items if str(item.get("id")) != str(item_id)]
    save_json(INVENTORY_FILE, updated)
    return redirect(url_for("inventory"))

@app.route("/delete_sale/<source>/<int:idx>", methods=["POST"])
def delete_sale(source, idx):
    """Delete an individual sale record by list index."""
    if "user" not in session: return redirect(url_for("login"))
    if source == "archive":
        records = load_json(ARCHIVE_FILE)
        real_idx = len(records) - 1 - idx
        if 0 <= real_idx < len(records):
            del records[real_idx]
            save_json(ARCHIVE_FILE, records)
        return redirect(url_for("sales_archive"))
    else:
        records = load_json(SALES_FILE)
        if 0 <= idx < len(records):
            del records[idx]
            save_json(SALES_FILE, records)
        return redirect(url_for("sales"))

@app.route("/sales")
def sales():
    if "user" not in session: return redirect(url_for("login"))
    current_week_sales, archived_count = auto_archive_old_sales()
    monday = get_current_week_monday()
    sunday = monday + timedelta(days=6)
    week_label = f"{monday.strftime('%B %d')} \u2013 {sunday.strftime('%B %d, %Y')}"
    # ── Pass the full archive so the JS week-cards can show historical data ──
    all_archived = load_json(ARCHIVE_FILE)
    return render_template(
        "sales.html",
        sales=current_week_sales,
        archive_sales=all_archived,          # <-- needed for past-week cards
        products=load_json(INVENTORY_FILE),
        week_label=week_label,
        total_archived=len(all_archived),
        is_archive_view=False
    )

@app.route("/sales/archive")
def sales_archive():
    if "user" not in session: return redirect(url_for("login"))
    archived = list(reversed(load_json(ARCHIVE_FILE)))
    monday = get_current_week_monday()
    sunday = monday + timedelta(days=6)
    week_label = f"{monday.strftime('%B %d')} \u2013 {sunday.strftime('%B %d, %Y')}"
    return render_template(
        "sales.html",
        sales=archived,
        archive_sales=archived,
        products=load_json(INVENTORY_FILE),
        week_label=week_label,
        total_archived=len(archived),
        is_archive_view=True
    )

@app.route("/forecast")
def forecast():
    """
    Per-week sales bar chart + 7-day ARIMA forecast.
    Accepts ?week=YYYY-MM-DD to show a specific week.
    Defaults to the current week.
    """
    if "user" not in session: return redirect(url_for("login"))

    live_sales     = load_json(SALES_FILE)
    archived_sales = load_json(ARCHIVE_FILE)
    all_sales      = archived_sales + live_sales

    categories_list = ["Jackets", "T-shirts", "Shorts"]
    current_monday  = get_current_week_monday()

    # ── Resolve the selected week ─────────────────────────────────────
    week_param = request.args.get("week", "").strip()
    if week_param:
        try:
            week_start = datetime.strptime(week_param, "%Y-%m-%d").date()
            # Snap to Monday of that week
            week_start = week_start - timedelta(days=week_start.weekday())
        except ValueError:
            week_start = current_monday
    else:
        week_start = current_monday

    week_end = week_start + timedelta(days=6)

    # ── Build daily totals for the selected 7-day window ─────────────
    daily_data = {}
    for i in range(7):
        day = week_start + timedelta(days=i)
        daily_data[day.strftime("%Y-%m-%d")] = {cat: 0 for cat in categories_list}

    for sale in all_sales:
        date_str = sale.get("datetime", "")[:10]
        try:
            sale_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        category = get_category_from_sale(sale)
        qty      = int(sale.get("quantity", 0))
        key      = sale_date.strftime("%Y-%m-%d")
        if category in categories_list and key in daily_data:
            daily_data[key][category] += qty

    # ── Available weeks for the week-selector dropdown ────────────────
    available_weeks = get_all_weeks()

    selected_week_str = week_start.strftime("%Y-%m-%d")
    is_current_week   = (week_start == current_monday)
    week_label        = f"{week_start.strftime('%b %d')} – {week_end.strftime('%b %d, %Y')}"

    return render_template(
        "forecast.html",
        daily_data       = daily_data,
        prediction_data  = daily_data,      # ARIMA uses the same 7-day week
        date_range_label = week_label,
        selected_week    = selected_week_str,
        is_current_week  = is_current_week,
        available_weeks  = available_weeks
    )

@app.route("/pos")
def pos():
    if "user" not in session: return redirect(url_for("login"))
    return render_template("pos.html")

@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False)
