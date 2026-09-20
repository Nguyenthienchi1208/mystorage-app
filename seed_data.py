import random
import datetime
import uuid
import os
import psycopg2

def _date_key(d: datetime.date) -> int:
    return int(d.strftime("%Y%m%d"))

def seed_database_large(conn, days_to_seed=365):
    cur = conn.cursor()

    cur.execute("SELECT COUNT(1) FROM dim_warehouse_slot")
    if cur.fetchone()[0] > 0:
        print("Database already seeded!")
        conn.commit()
        return

    today = datetime.date.today()
    start_date = today - datetime.timedelta(days=days_to_seed)

    print(f"🚀 Starting Large Seeding from {start_date} to {today} ({days_to_seed} days)...")

    # 1. Seed dim_date cho toàn bộ 365 ngày
    date_list = [start_date + datetime.timedelta(days=x) for x in range(days_to_seed + 1)]
    for d in date_list:
        dk = _date_key(d)
        cur.execute(
            """INSERT INTO dim_date(date_key, full_date, day_of_week, day_name, month, quarter, year, is_weekend) 
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (date_key) DO NOTHING""",
            (dk, d, d.isoweekday(), d.strftime("%A"), d.month, (d.month - 1) // 3 + 1, d.year, d.weekday() >= 5)
        )

    # 2. Seed Warehouses & Slots ENRICHED (Mở rộng lên ~210 Slots & 8 Kho)
    warehouses = [
        {"id": "WH-Q2", "name": "MyStorage An Phú HQ", "district": "Quận 2", "lat": 10.7900, "lng": 106.7200, "slots": 40},
        {"id": "WH-TB", "name": "MyStorage Tân Bình", "district": "Tân Bình", "lat": 10.7910, "lng": 106.6470, "slots": 30},
        {"id": "WH-Q7", "name": "Ministop Tân Thuận", "district": "Quận 7", "lat": 10.7380, "lng": 106.7190, "slots": 30},
        {"id": "WH-Q9", "name": "Ministop Tăng Nhơn Phú", "district": "Quận 9", "lat": 10.8470, "lng": 106.8060, "slots": 25},
        {"id": "WH-Q1", "name": "MyStorage Bến Nghé", "district": "Quận 1", "lat": 10.7769, "lng": 106.7009, "slots": 20},
        {"id": "WH-TD", "name": "MyStorage Linh Trung", "district": "Thủ Đức", "lat": 10.8650, "lng": 106.7780, "slots": 25},
        {"id": "WH-BT", "name": "MyStorage Điện Biên Phủ", "district": "Bình Thạnh", "lat": 10.8010, "lng": 106.7110, "slots": 20},
        {"id": "WH-Q3", "name": "Ministop Nam Kỳ Khởi Nghĩa", "district": "Quận 3", "lat": 10.7840, "lng": 106.6890, "slots": 15},
    ]

    slot_keys = []
    for w in warehouses:
        for i in range(1, w["slots"] + 1):
            slot_id = f"{w['id']}-S{i:03d}"
            unit_type = random.choices(["Locker", "Mini", "Medium", "Large"], weights=[15, 35, 35, 15])[0]
            vol = round(random.uniform(1.0, 20.0), 2)
            area = round(random.uniform(1.0, 25.0), 2)
            env = random.choice(["Air-Conditioned", "Standard Dry", "Temperature Controlled"])
            access_type = random.choice(["24/7 Access", "Standard Business Hours", "Keycard Access"])
            
            cur.execute(
                """INSERT INTO dim_warehouse_slot(slot_id, slot_code, unit_type, volume_cbm, slot_area_sqm, 
                   storage_environment, access_type, warehouse_id, warehouse_name, hub_type, address, district, latitude, longitude) 
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING slot_key""",
                (
                    slot_id, f"{w['id'][:3]}-{i:03d}", unit_type, vol, area, 
                    env, access_type, w['id'], w['name'], "Local Hub", 
                    f"Số {i*3} Đường chính, {w['district']}", w['district'], w['lat'], w['lng']
                )
            )
            slot_keys.append(cur.fetchone()[0])

    # 3. Seed Items ENRICHED (Mở rộng 30 SKUs phong phú chủng loại)
    item_catalog = [
        ("Thùng Carton Tiêu Chuẩn", "Bao bì & Đóng gói", "Thùng", 0.5, 0.05),
        ("Thùng Hồ Sơ Tài Liệu", "Tài liệu Văn phòng", "Hộp", 2.0, 0.08),
        ("Nội Thất Văn Phòng Bàn Ghế", "Nội thất", "Bộ", 25.0, 1.20),
        ("Thiết Bị Điện Tử IT", "Điện máy", "Cái", 8.0, 0.15),
        ("Quần Áo Thời Trang Tồn Kho", "May mặc", "Kiện", 15.0, 0.40),
        ("Đồ Dùng Gia Đình Cá Nhân", "Đồ cá nhân", "Thùng", 10.0, 0.25),
        ("Vật Tư Sân Sân Sân Sân", "Thiết bị sự kiện", "Kiện", 30.0, 1.50),
        ("Máy Máy In & Thiết Bị Văn Phòng", "Thiết bị", "Cái", 12.0, 0.30),
        ("Thùng Sơn & Đồ Kim Khí", "Xây dựng", "Thùng", 18.0, 0.10),
        ("Xe Đạp / Dụng Cụ Thể Thao", "Thể thao", "Cái", 14.0, 0.80),
    ]

    item_keys = []
    for i in range(1, 31):
        cat_info = item_catalog[(i - 1) % len(item_catalog)]
        cur.execute(
            """INSERT INTO dim_item(sku_code, item_name, item_category, unit, weight_kg, volume_cbm) 
               VALUES (%s,%s,%s,%s,%s,%s) RETURNING item_key""",
            (
                f"SKU-{i:04d}", 
                f"{cat_info[0]} Model-{chr(65 + (i%5))}", 
                cat_info[1], 
                cat_info[2], 
                round(cat_info[3] * random.uniform(0.8, 1.2), 2), 
                round(cat_info[4] * random.uniform(0.9, 1.1), 2)
            )
        )
        item_keys.append(cur.fetchone()[0])

    # 4. Seed Contracts ENRICHED (Mở rộng 200 Hợp đồng)
    company_names = ["Shopee Mall Store", "Tiki Logistics", "Giao Hàng Nhanh", "Sendo Merchant", "FPT Retail", "Concung Corp", "Phúc Long Coffee", "Highlands Coffee", "Tập đoàn An Gia", "Cty Thiết kế Deco"]
    industries = ["E-commerce", "Retail", "F&B", "Interior Design", "Logistics", "IT Hardware", "Fashion", "Event Management"]
    
    contract_keys = []
    for i in range(1, 201):
        cid = f"CTR-{i:05d}"
        c_name = f"{random.choice(company_names)} {i}"
        c_type = random.choice(["Enterprise", "SME", "Individual"])
        ind = random.choice(industries)
        rent_model = random.choice(["Long-term", "Short-term", "On-Demand"])
        billing_cycle = random.choice(["Monthly", "Quarterly", "Annually"])
        
        s_date = start_date + datetime.timedelta(days=random.randint(0, 200))
        e_date = s_date + datetime.timedelta(days=random.choice([90, 180, 365, 730]))
        unit_price = random.choice([300000, 500000, 800000, 1200000, 2500000])
        deposit = unit_price * random.choice([1, 2])
        
        cur.execute(
            """INSERT INTO dim_customer_contract(contract_id, customer_id, customer_name, customer_type, industry, rental_model, billing_cycle, rent_unit_price, deposit_amount, start_date, end_date, is_active) 
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING contract_key""",
            (cid, f"CUST-{i:04d}", c_name, c_type, ind, rent_model, billing_cycle, unit_price, deposit, s_date, e_date, True)
        )
        contract_keys.append(cur.fetchone()[0])

    # 5. Seed Fact Inventory Snapshot cho 365 Ngày
    print("📦 Generating Daily Inventory Snapshots...")
    snapshot_rows = []
    for d in date_list:
        dk = _date_key(d)
        for sk in slot_keys:
            # 82% tỷ lệ lấp đầy
            is_empty = random.choices([0, 1], weights=[82, 18])[0]
            status = "OCCUPIED" if is_empty == 0 else "EMPTY"
            ck = random.choice(contract_keys) if is_empty == 0 else None
            ik = random.choice(item_keys) if is_empty == 0 else None
            vol = round(random.uniform(2.0, 12.0), 2) if is_empty == 0 else 0.0
            area = round(random.uniform(1.5, 10.0), 2) if is_empty == 0 else 0.0
            
            snapshot_rows.append((d, dk, sk, ck, ik, status, is_empty, vol, area))

    # Bulk Insert Snapshots theo lô 10,000 dòng để tối ưu hiệu năng
    batch_size = 10000
    for i in range(0, len(snapshot_rows), batch_size):
        cur.executemany(
            """INSERT INTO fact_inventory_snapshot(snapshot_date, date_key, slot_key, contract_key, item_key, slot_status, is_empty, occupied_volume_cbm, occupied_area_sqm)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", snapshot_rows[i:i+batch_size]
        )

    # 6. Seed Fact Transactions & VAS Charges ENRICHED
    print("🚚 Generating Transactions & VAS Charges...")
    vas_services = [
        ("PACKING", 50000), 
        ("HANDLING", 100000), 
        ("TRANSPORT", 250000), 
        ("LABELING", 20000), 
        ("INSPECTION", 150000)
    ]

    for d in date_list:
        dk = _date_key(d)
        
        # Sinh 10-25 giao dịch kho mỗi ngày
        num_tx = random.randint(10, 25)
        for _ in range(num_tx):
            txid = f"TX-{uuid.uuid4().hex[:10].upper()}"
            ttime = datetime.datetime.combine(d, datetime.time(random.randint(7, 19), random.randint(0, 59)))
            qty = random.randint(1, 50)
            weight = round(qty * random.uniform(2.0, 8.0), 2)
            vol = round(qty * random.uniform(0.05, 0.3), 2)
            
            cur.execute(
                """INSERT INTO fact_warehouse_transaction(transaction_id, date_key, transaction_time, slot_key, contract_key, item_key, transaction_type, quantity, total_weight_kg, total_volume_cbm)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (txid, dk, ttime, random.choice(slot_keys), random.choice(contract_keys), random.choice(item_keys), random.choice(["INBOUND", "OUTBOUND"]), qty, weight, vol)
            )

        # Sinh thêm 3-8 hóa đơn Dịch vụ gia tăng (VAS Charges) mỗi ngày
        num_vas = random.randint(3, 8)
        for _ in range(num_vas):
            vas_id = f"VAS-{uuid.uuid4().hex[:10].upper()}"
            vas_time = datetime.datetime.combine(d, datetime.time(random.randint(8, 17), random.randint(0, 59)))
            service = random.choice(vas_services)
            quantity = random.randint(1, 5)
            total_amount = service[1] * quantity

            # Giả định bảng fact_vas_charge tồn tại trong Schema của bạn
            cur.execute(
                """INSERT INTO fact_vas_charge(vas_charge_id, date_key, charge_time, slot_key, contract_key, service_type, unit_price, quantity, total_amount)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (vas_id, dk, vas_time, random.choice(slot_keys), random.choice(contract_keys), service[0], service[1], quantity, total_amount)
            )

    conn.commit()
    print("✅ Large Seeding Complete!")

if __name__ == "__main__":
    cfg = {
        "host": os.getenv("DB_HOST", "localhost"),
        "dbname": os.getenv("DB_NAME", "mystorage_db"),
        "user": os.getenv("DB_USER", "myuser"),
        "password": os.getenv("DB_PASSWORD", "mypassword"),
        "port": int(os.getenv("DB_PORT", 5433)),
    }
    conn = psycopg2.connect(**cfg)
    try:
        seed_database_large(conn, days_to_seed=365)
    finally:
        conn.close()