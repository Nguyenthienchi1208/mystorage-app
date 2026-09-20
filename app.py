import os
import re
import traceback
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import psycopg2
import psycopg2.extras
import streamlit as st
import difflib
from db import ensure_tables, fetch_df, get_conn

# Import hàm seed từ file seed_data
try:
    from seed_data import seed_database_large as seed_database
except ImportError:
    from seed_data import seed_database


# -----------------------------------------------------------------------------
# 1. CẤU HÌNH TRANG STREAMLIT
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="MyStorage Analytics & AI Assistant",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)


# -----------------------------------------------------------------------------
# 2. QUẢN LÝ KẾT NỐI DATABASE
# -----------------------------------------------------------------------------
def connect_db_or_die():
    try:
        conn_ctx = get_conn()
        conn = conn_ctx.__enter__()
        return conn, conn_ctx
    except Exception as e:
        st.error(
            "Cannot connect to database. Check environment variables and DB status."
        )
        st.exception(e)
        raise


def run_query(conn, query: str) -> pd.DataFrame:
    try:
        df = fetch_df(conn, query)
        return df
    except Exception as e:
        st.error(f"Lỗi truy vấn Database: {e}")
        return pd.DataFrame()


# Helper: map approximate volume to unit_type
def map_volume_to_unit_type(volume_cbm: float) -> str:
    if volume_cbm <= 0.5:
        return "Locker"
    if volume_cbm <= 5:
        return "Mini"
    if volume_cbm <= 20:
        return "Medium"
    return "Large"


# Helper: parse months duration from query text
def parse_months_from_text(text: str) -> int:
    # support forms: "6 tháng", "6tháng", "6 tháng", "1 năm", "nửa năm", "6m", "6 tháng" etc.
    text = text.lower()
    # explicit month numbers
    m = re.search(r"(\d+)\s*(?:tháng|m|months?)\b", text)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            pass

    # year -> months
    y = re.search(r"(\d+)\s*(?:năm|year)s?\b", text)
    if y:
        try:
            return int(y.group(1)) * 12
        except Exception:
            pass

    if "nửa năm" in text or "0.5 năm" in text:
        return 6
    if "một năm" in text or "1 năm" in text:
        return 12

    # default
    return 1


# Improved volume extractor (handles m3, m^3, mét khối, m³)
def extract_volume_from_text(text: str):
    text = text.lower()
    # common patterns: 20m3, 20 m3, 20 m^3, 20 mét khối, 20m³
    m = re.search(r"(\d+(?:[\.,]\d+)?)\s*(?:m\^?3|m3|m\u00B3|mét khối|met khoi)\b", text)
    if m:
        try:
            return float(m.group(1).replace(',', '.'))
        except Exception:
            return None
    return None


# Normalize a district string using a small alias map + fuzzy match
DISTRICT_ALIASES = {
    "q1": "QUẬN 1",
    "q2": "QUẬN 2",
    "quận 1": "QUẬN 1",
    "quận 2": "QUẬN 2",
    "tân bình": "TÂN BÌNH",
    "tân bình": "TÂN BÌNH",
    "bình thạnh": "BÌNH THẠNH",
    "thủ đức": "THỦ ĐỨC",
    "quận 7": "QUẬN 7",
}

def normalize_district(text: str):
    if not text:
        return None
    t = text.strip().lower()
    if t in DISTRICT_ALIASES:
        return DISTRICT_ALIASES[t]
    # fuzzy match against known alias keys
    keys = list(DISTRICT_ALIASES.keys())
    matches = difflib.get_close_matches(t, keys, n=1, cutoff=0.7)
    if matches:
        return DISTRICT_ALIASES[matches[0]]
    # uppercase fallback
    return text.upper()


# Fuzzy customer matcher: fetches distinct names and picks a close match
def fuzzy_match_customer(conn, name_fragment: str):
    try:
        df = fetch_df(conn, "SELECT DISTINCT customer_name FROM dim_customer_contract WHERE customer_name IS NOT NULL")
        if df.empty:
            return None
        names = df["customer_name"].dropna().astype(str).tolist()
        # try close matches
        candidates = difflib.get_close_matches(name_fragment, names, n=1, cutoff=0.6)
        return candidates[0] if candidates else None
    except Exception:
        return None


# Helper: compute pricing (avg monthly) and apply simple discount & deposit rules
def compute_pricing(conn, unit_type: str, months: int):
    sql = """
        SELECT ROUND(AVG(c.rent_unit_price)::numeric, 0) as avg_monthly
        FROM dim_customer_contract c
        JOIN fact_inventory_snapshot f ON c.contract_key = f.contract_key
        JOIN dim_warehouse_slot s ON f.slot_key = s.slot_key
        WHERE s.unit_type = %s
    """
    dfp = fetch_df(conn, sql, (unit_type,))
    avg_monthly = (
        int(dfp["avg_monthly"].iloc[0]) if not dfp.empty and pd.notnull(dfp["avg_monthly"].iloc[0]) else 0
    )

    # simple discount rules
    discount_pct = 0
    if months >= 12:
        discount_pct = 0.15
    elif months >= 6:
        discount_pct = 0.10

    deposit_months = 1  # default policy

    total_before = avg_monthly * months
    discount_amount = int(total_before * discount_pct)
    total_after = total_before - discount_amount
    required_deposit = avg_monthly * deposit_months

    return {
        "avg_monthly": avg_monthly,
        "months": months,
        "discount_pct": discount_pct,
        "discount_amount": discount_amount,
        "total_before": total_before,
        "total_after": total_after,
        "deposit_months": deposit_months,
        "required_deposit": required_deposit,
    }


# Helper: generate short Zalo message
def generate_zalo_message(unit_type: str, pricing_info: dict):
    monthly = pricing_info.get("avg_monthly", 0)
    discount_pct = int(pricing_info.get("discount_pct", 0) * 100)
    deposit = pricing_info.get("deposit_months", 1)
    total_after = pricing_info.get("total_after", 0)

    msg = (
        f"Kính gửi anh/chị, giá thuê tham khảo cho kho {unit_type}: {monthly:,.0f} VNĐ/tháng. "
        f"Ưu đãi: giảm {discount_pct}% khi thuê {pricing_info['months']} tháng. "
        f"Tiền cọc: {deposit} tháng = {pricing_info['required_deposit']:,.0f} VNĐ. "
        f"Tổng sau giảm: {total_after:,.0f} VNĐ. Liên hệ để book ngay ạ!"
    )
    return msg


# -----------------------------------------------------------------------------
# 3. AI CHATBOT QUERY LOGIC (XỬ LÝ 100% KỊCH BẢN)
# -----------------------------------------------------------------------------
def run_chat_query_db(conn, user_query: str):
    q_lower = user_query.lower()
    executed_sql = ""
    df = pd.DataFrame()
    answer = ""

    try:
        # 1. Trích xuất Loại kho (Unit Type)
        unit_type = None
        for ut in ["locker", "mini", "medium", "large"]:
            if ut in q_lower:
                unit_type = ut.capitalize()
                break

        # 2. Trích xuất Quận từ câu hỏi
        district_search = None
        q_match = re.search(
            r"(quận\s*\d+|q\d+|tân bình|bình thạnh|thủ đức|gò vấp|phú nhuận|tân phú|bình tân|quận 7|quận 2|quận 9)",
            q_lower,
        )
        if q_match:
            raw_district = q_match.group(0)
            if raw_district.startswith("q") and raw_district[1:].isdigit():
                district_search = f"QUẬN {raw_district[1:]}"
            else:
                district_search = raw_district.upper()

        # ----------------------------------------------------
        # PHÂN TÍCH VÀ ĐIỀU HƯỚNG CÂU HỎI CHATBOT
        # ----------------------------------------------------

        # Phân tích Doanh thu & Top Khách hàng
        if any(
            k in q_lower
            for k in [
                "khách hàng nào mang lại doanh thu cao nhất",
                "doanh thu cao nhất",
                "hợp đồng lớn nhất",
                "top khách hàng",
                "doanh thu hợp đồng",
                "khách hàng chi tiêu nhiều",
            ]
        ):
            executed_sql = """
            SELECT customer_name, customer_type, industry, rent_unit_price as mrr_vnd, start_date, end_date
            FROM dim_customer_contract
            ORDER BY rent_unit_price DESC
            LIMIT 10
            """
            df = fetch_df(conn, executed_sql)
            answer = "🏆 **Top 10 Khách hàng & Hợp đồng mang lại Doanh thu / MRR cao nhất:**"

        # Phân tích Ngành nghề / Lĩnh vực
        elif any(
            k in q_lower
            for k in [
                "ngành nghề nào thuê kho nhiều nhất",
                "lĩnh vực nào chiếm doanh thu",
                "ngành nghề",
                "khách hàng theo ngành",
                "lĩnh vực thuê",
            ]
        ):
            executed_sql = """
            SELECT industry as nganh_nghe, 
                   COUNT(contract_id) as so_luong_hop_dong, 
                   SUM(rent_unit_price) as tong_doanh_thu_mrr,
                   ROUND(AVG(rent_unit_price)::numeric, 0) as gia_trung_binh
            FROM dim_customer_contract
            GROUP BY industry
            ORDER BY tong_doanh_thu_mrr DESC
            """
            df = fetch_df(conn, executed_sql)
            answer = "📊 **Thống kê Doanh thu & Tỷ lệ thuê kho phân theo Ngành nghề / Lĩnh vực:**"

        # Phân loại Khách hàng (SME / Cá nhân / Enterprise)
        elif any(
            k in q_lower
            for k in [
                "bao nhiêu hợp đồng doanh nghiệp",
                "loại hình khách hàng",
                "sme",
                "cá nhân",
                "phân loại khách hàng",
            ]
        ):
            executed_sql = """
            SELECT customer_type as loai_khach_hang, 
                   COUNT(contract_id) as so_hop_dong, 
                   SUM(rent_unit_price) as tong_mrr_vnd,
                   ROUND(AVG(rent_unit_price)::numeric, 0) as gia_trung_binh_vnd
            FROM dim_customer_contract
            GROUP BY customer_type
            ORDER BY tong_mrr_vnd DESC
            """
            df = fetch_df(conn, executed_sql)
            answer = "👥 **Thống kê Cơ cấu Hợp đồng theo Loại hình Khách hàng:**"

        # Cảnh báo Hợp đồng sắp hết hạn
        elif any(
            k in q_lower
            for k in [
                "sắp hết hạn",
                "hợp đồng sắp hết hạn",
                "gia hạn hợp đồng",
                "hết hạn thuê",
            ]
        ):
            executed_sql = """
            SELECT contract_id, customer_name, customer_type, end_date, rent_unit_price, is_active
            FROM dim_customer_contract
            WHERE end_date >= CURRENT_DATE
            ORDER BY end_date ASC
            LIMIT 10
            """
            df = fetch_df(conn, executed_sql)
            answer = "⚠️ **Danh sách Hợp đồng sắp hết hạn gần nhất (Cần ưu tiên gia hạn):**"

        # Phân tích Dịch vụ VAS
        elif any(
            k in q_lower
            for k in [
                "vas",
                "dịch vụ gia tăng",
                "đóng gói",
                "vận chuyển",
                "doanh thu vas",
                "dịch vụ vas",
            ]
        ):
            executed_sql = """
            SELECT service_type as loai_dich_vu, 
                   COUNT(*) as so_luot_su_dung, 
                   SUM(total_amount) as tong_doanh_thu_vas_vnd,
                   ROUND(AVG(total_amount)::numeric, 0) as chi_phi_trung_binh
            FROM fact_vas_charge
            GROUP BY service_type
            ORDER BY tong_doanh_thu_vas_vnd DESC
            """
            df = fetch_df(conn, executed_sql)
            answer = "🚚 **Thống kê Doanh thu & Tần suất sử dụng Dịch vụ Gia tăng (VAS):**"

        # Giao dịch Nhập / Xuất kho
        elif any(
            k in q_lower
            for k in [
                "nhập xuất",
                "giao dịch",
                "tần suất giao dịch",
                "luân chuyển",
                "xuất nhập nhiều nhất",
            ]
        ):
            executed_sql = """
            SELECT s.warehouse_name, 
                   t.transaction_type as loai_giao_dich, 
                   COUNT(*) as tong_so_giao_dich
            FROM fact_warehouse_transaction t
            JOIN dim_warehouse_slot s ON t.slot_key = s.slot_key
            GROUP BY s.warehouse_name, t.transaction_type
            ORDER BY s.warehouse_name, tong_so_giao_dich DESC
            """
            df = fetch_df(conn, executed_sql)
            answer = "🔄 **Thống kê Tần suất Giao dịch Nhập / Xuất theo từng Kho:**"

        # Phân tích SKU / Danh mục hàng hóa
        elif any(
            k in q_lower
            for k in [
                "sku",
                "mặt hàng nào lưu kho nhiều nhất",
                "danh mục hàng hóa",
                "hàng hóa",
                "mặt hàng",
                "danh mục",
            ]
        ):
            executed_sql = """
            SELECT i.item_category as danh_muc_hang, 
                   COUNT(f.snapshot_key) as so_luong_slot_dang_chua,
                   COUNT(DISTINCT i.item_key) as so_sku_khac_nhau
            FROM fact_inventory_snapshot f
            JOIN dim_item i ON f.item_key = i.item_key
            WHERE f.is_empty = 0
            GROUP BY i.item_category
            ORDER BY so_luong_slot_dang_chua DESC
            """
            df = fetch_df(conn, executed_sql)
            answer = "📦 **Thống kê Danh mục Hàng hóa & SKU đang lưu kho:**"

        # Kho Máy lạnh / Điều hòa
        elif any(
            k in q_lower
            for k in [
                "máy lạnh",
                "điều hòa",
                "nhiệt độ chuẩn",
                "air-conditioned",
                "môi trường",
                "máy lạnh còn trống",
            ]
        ):
            # Build a conditional query to allow combining district / availability filters
            conditions = ["(s.storage_environment ILIKE %s OR s.storage_environment ILIKE %s OR s.storage_environment ILIKE %s)"]
            params = ["%Air%", "%Cold%", "%Lạnh%"]

            # if user asked for district, normalize and add filter
            if district_search:
                nd = normalize_district(district_search)
                conditions.append("s.district ILIKE %s")
                params.append(f"%{nd}%")

            # if user asked for availability (words like 'trống'), filter is_empty
            if any(tok in q_lower for tok in ["trống", "còn trống", "slot trống"]):
                conditions.append("(f.is_empty = 1 OR f.is_empty IS NULL)")

            where_clause = " AND ".join(conditions)
            executed_sql = f"""
            SELECT s.warehouse_name, s.district, s.storage_environment,
                   COUNT(s.slot_id) as tong_slot,
                   SUM(CASE WHEN f.is_empty = 1 OR f.is_empty IS NULL THEN 1 ELSE 0 END) as slot_trong
            FROM dim_warehouse_slot s
            LEFT JOIN fact_inventory_snapshot f ON s.slot_key = f.slot_key AND f.snapshot_date = CURRENT_DATE
            WHERE {where_clause}
            GROUP BY s.warehouse_name, s.district, s.storage_environment
            ORDER BY slot_trong DESC
            """
            df = fetch_df(conn, executed_sql, tuple(params) if params else None)
            answer = "❄️ **Tình trạng sức chứa các Kho Điều Hòa / Máy Lạnh (Air-Conditioned / Climate Controlled):**"

        # Tỷ lệ Lấp đầy (Occupancy Rate)
        elif any(
            k in q_lower
            for k in [
                "tỷ lệ lấp đầy",
                "kho nào đầy nhất",
                "hiệu suất kho",
                "occupancy",
                "lấp đầy",
            ]
        ):
            executed_sql = """
            SELECT s.warehouse_name, s.district,
                   COUNT(s.slot_id) as tong_slot,
                   SUM(CASE WHEN f.is_empty = 0 THEN 1 ELSE 0 END) as slot_da_thue,
                   ROUND((SUM(CASE WHEN f.is_empty = 0 THEN 1.0 ELSE 0.0 END) / COUNT(s.slot_id) * 100)::numeric, 1) as ty_le_lap_day_pct
            FROM dim_warehouse_slot s
            LEFT JOIN fact_inventory_snapshot f ON s.slot_key = f.slot_key AND f.snapshot_date = CURRENT_DATE
            GROUP BY s.warehouse_name, s.district
            ORDER BY ty_le_lap_day_pct DESC
            """
            df = fetch_df(conn, executed_sql)
            answer = "📈 **Báo cáo Tỷ lệ Lấp đầy (Occupancy Rate) chi tiết từng Chi nhánh Kho:**"

        # Kho còn trống nhiều nhất
        elif any(
            k in q_lower
            for k in [
                "chỗ nào còn",
                "kho nào còn",
                "trống nhiều nhất",
                "còn nhiều kho",
                "ở đâu còn",
                "kho nào sẵn sàng",
            ]
        ):
            executed_sql = """
            SELECT s.warehouse_name, s.district, 
                   COUNT(s.slot_id) as tong_so_slot,
                   SUM(CASE WHEN f.is_empty = 1 OR f.is_empty IS NULL THEN 1 ELSE 0 END) as so_slot_con_trong
            FROM dim_warehouse_slot s
            LEFT JOIN fact_inventory_snapshot f ON s.slot_key = f.slot_key AND f.snapshot_date = CURRENT_DATE
            GROUP BY s.warehouse_name, s.district
            ORDER BY so_slot_con_trong DESC
            """
            df = fetch_df(conn, executed_sql)
            answer = "🔥 **Thống kê các địa điểm kho còn nhiều slot trống nhất hiện tại:**"

        # Vị trí / Địa chỉ kho gần nhất
        elif any(
            k in q_lower
            for k in [
                "gần nhất",
                "ở đâu",
                "địa chỉ",
                "vị trí kho",
                "chi nhánh",
                "kho gần",
            ]
        ):
            if district_search:
                executed_sql = """
                SELECT s.warehouse_name, s.district, COUNT(s.slot_id) as tong_slot,
                       SUM(CASE WHEN f.is_empty = 1 OR f.is_empty IS NULL THEN 1 ELSE 0 END) as slot_trong
                FROM dim_warehouse_slot s
                LEFT JOIN fact_inventory_snapshot f ON s.slot_key = f.slot_key AND f.snapshot_date = CURRENT_DATE
                WHERE s.district ILIKE %s
                GROUP BY s.warehouse_name, s.district
                """
                df = fetch_df(conn, executed_sql, (f"%{district_search}%",))
                if not df.empty:
                    answer = f"📍 Tìm thấy **{len(df)}** chi nhánh kho phục vụ khu vực **{district_search}**:"
                else:
                    executed_sql_all = """
                    SELECT s.warehouse_name, s.district, COUNT(s.slot_id) as tong_slot,
                           SUM(CASE WHEN f.is_empty = 1 OR f.is_empty IS NULL THEN 1 ELSE 0 END) as slot_trong
                    FROM dim_warehouse_slot s
                    LEFT JOIN fact_inventory_snapshot f ON s.slot_key = f.slot_key AND f.snapshot_date = CURRENT_DATE
                    GROUP BY s.warehouse_name, s.district
                    """
                    df = fetch_df(conn, executed_sql_all)
                    answer = f"Hiện MyStorage chưa có kho trực tiếp tại **{district_search}**. Dưới đây là danh sách các chi nhánh kho gần bạn nhất:"
            else:
                executed_sql = """
                SELECT s.warehouse_name, s.district, COUNT(s.slot_id) as tong_slot,
                       SUM(CASE WHEN f.is_empty = 1 OR f.is_empty IS NULL THEN 1 ELSE 0 END) as slot_trong
                FROM dim_warehouse_slot s
                LEFT JOIN fact_inventory_snapshot f ON s.slot_key = f.slot_key AND f.snapshot_date = CURRENT_DATE
                GROUP BY s.warehouse_name, s.district
                ORDER BY s.district
                """
                df = fetch_df(conn, executed_sql)
                answer = "📍 **Danh sách các vị trí/chi nhánh kho của MyStorage:**"

        # Tra cứu Giá thuê
        elif "giá" in q_lower or "giá thuê" in q_lower:
            if unit_type:
                executed_sql = """
                SELECT s.unit_type, 
                       ROUND(AVG(c.rent_unit_price)::numeric, 0) as gia_trung_binh,
                       MIN(c.rent_unit_price) as gia_thap_nhat,
                       MAX(c.rent_unit_price) as gia_cao_nhat,
                       COUNT(c.contract_id) as so_hop_dong
                FROM dim_customer_contract c
                JOIN fact_inventory_snapshot f ON c.contract_key = f.contract_key
                JOIN dim_warehouse_slot s ON f.slot_key = s.slot_key
                WHERE s.unit_type = %s
                GROUP BY s.unit_type
                """
                df = fetch_df(conn, executed_sql, (unit_type,))
                answer = (
                    f"💰 Bảng giá thuê tham khảo cho loại kho **{unit_type}**:"
                )
            else:
                executed_sql = """
                SELECT s.unit_type, 
                       ROUND(AVG(c.rent_unit_price)::numeric, 0) as gia_trung_binh_vnd,
                       MIN(c.rent_unit_price) as gia_thap_nhat_vnd,
                       MAX(c.rent_unit_price) as gia_cao_nhat_vnd
                FROM dim_customer_contract c
                JOIN fact_inventory_snapshot f ON c.contract_key = f.contract_key
                JOIN dim_warehouse_slot s ON f.slot_key = s.slot_key
                GROUP BY s.unit_type
                ORDER BY gia_trung_binh_vnd DESC
                """
                df = fetch_df(conn, executed_sql)
                answer = "💰 Bảng giá thuê trung bình phân theo từng loại kho (Unit Type):"

        # Tính toán Tổng chi phí + Chiết khấu + Tiền cọc
        elif any(k in q_lower for k in ["tính giúp", "tính giúp tôi", "tổng chi phí", "tính tổng chi phí"]):
            months = parse_months_from_text(q_lower)
            # try to detect unit_type by keywords or volume
            unit_type = None
            for ut in ["locker", "mini", "medium", "large"]:
                if ut in q_lower:
                    unit_type = ut.capitalize()
                    break
            if not unit_type:
                vol_match = re.search(r"(\d+(?:\.\d+)?)\s*m\^?3|m3|m\u00B3", user_query)
                if vol_match:
                    try:
                        vol = float(vol_match.group(1))
                        unit_type = map_volume_to_unit_type(vol)
                    except Exception:
                        unit_type = "Medium"
                else:
                    unit_type = "Large"

            pricing = compute_pricing(conn, unit_type, months)
            executed_sql = "-- computed pricing using avg from dim_customer_contract"
            answer = (
                f"💰 Tổng chi phí cho {unit_type} trong {months} tháng:\n"
                f"Giá trung bình (1 tháng): {pricing['avg_monthly']:,.0f} VNĐ\n"
                f"Tổng trước giảm: {pricing['total_before']:,.0f} VNĐ\n"
                f"Chiết khấu: {int(pricing['discount_pct']*100)}% = {pricing['discount_amount']:,.0f} VNĐ\n"
                f"Tổng sau giảm: {pricing['total_after']:,.0f} VNĐ\n"
                f"Tiền cọc ({pricing['deposit_months']} tháng): {pricing['required_deposit']:,.0f} VNĐ"
            )

        # Chiết khấu / Chính sách tiền cọc (FAQ)
        elif any(k in q_lower for k in ["chiết khấu", "chiết khấu bao nhiêu", "cọc", "tiền cọc"]):
            # Try to answer policy-like questions
            if "cọc" in q_lower or "tiền cọc" in q_lower:
                answer = (
                    "Chính sách hiện tại: Tiền cọc mặc định 1 tháng tiền thuê. "
                    "(Nếu cần thay đổi cho hợp đồng doanh nghiệp lớn, liên hệ Quản lý Sales để thỏa thuận.)"
                )
            elif "chiết" in q_lower:
                answer = (
                    "Chính sách chiết khấu tham khảo: thuê >=6 tháng giảm 10%, >=12 tháng giảm 15%. "
                    "Các chương trình khuyến mãi đặc biệt có thể áp dụng theo chiến dịch." 
                )

        # So sánh kho máy lạnh vs kho tiêu chuẩn
        elif any(k in q_lower for k in ["so sánh", "so sanh", "ưu điểm", "máy lạnh"]):
            # basic comparison using avg prices if available
            # detect unit size e.g. 10m3
            vol = extract_volume_from_text(user_query)
            target_size = map_volume_to_unit_type(vol) if vol is not None else None

            # fetch avg for air-conditioned and standard
            if target_size:
                sql_ac = """
                    SELECT ROUND(AVG(c.rent_unit_price)::numeric,0) as avg_monthly
                    FROM dim_customer_contract c
                    JOIN fact_inventory_snapshot f ON c.contract_key = f.contract_key
                    JOIN dim_warehouse_slot s ON f.slot_key = s.slot_key
                    WHERE s.unit_type = %s AND (s.storage_environment ILIKE '%%air%%' OR s.storage_environment ILIKE '%%cold%%' OR s.storage_environment ILIKE '%%lạnh%%')
                """
                sql_std = """
                    SELECT ROUND(AVG(c.rent_unit_price)::numeric,0) as avg_monthly
                    FROM dim_customer_contract c
                    JOIN fact_inventory_snapshot f ON c.contract_key = f.contract_key
                    JOIN dim_warehouse_slot s ON f.slot_key = s.slot_key
                    WHERE s.unit_type = %s AND (s.storage_environment NOT ILIKE '%%air%%' AND s.storage_environment NOT ILIKE '%%cold%%' AND s.storage_environment NOT ILIKE '%%lạnh%%')
                """
                df_ac = fetch_df(conn, sql_ac, (target_size,))
                df_std = fetch_df(conn, sql_std, (target_size,))
                ac_price = int(df_ac['avg_monthly'].iloc[0]) if not df_ac.empty and pd.notnull(df_ac['avg_monthly'].iloc[0]) else None
                std_price = int(df_std['avg_monthly'].iloc[0]) if not df_std.empty and pd.notnull(df_std['avg_monthly'].iloc[0]) else None

                comp_lines = []
                if ac_price:
                    comp_lines.append(f"Kho máy lạnh ({target_size}): ~{ac_price:,.0f} VNĐ/tháng")
                if std_price:
                    comp_lines.append(f"Kho tiêu chuẩn ({target_size}): ~{std_price:,.0f} VNĐ/tháng")
                comp_lines.append("Ưu điểm kho máy lạnh: bảo quản tốt cho hàng dễ hỏng, ít hư hỏng, phù hợp may mặc/ thực phẩm; giá thường cao hơn kho tiêu chuẩn.")
                answer = "\n".join(comp_lines)

        # Tra cứu hợp đồng cho khách hàng cụ thể
        elif re.search(r"khách hàng .* đang thuê|khách hàng .* thuê|khách hàng .* đang thuê những slot", q_lower):
            name_match = re.search(r"khách hàng\s+([\w\s\-\.&]+)", q_lower)
            if name_match:
                cust_name = name_match.group(1).strip()
                executed_sql = """
                    SELECT contract_id, customer_name, slot_key, start_date, end_date, rent_unit_price
                    FROM dim_customer_contract
                    WHERE customer_name ILIKE %s
                """
                df = fetch_df(conn, executed_sql, (f"%{cust_name}%",))
                # fallback: fuzzy match against known customer names
                if df.empty:
                    best = fuzzy_match_customer(conn, cust_name)
                    if best:
                        df = fetch_df(conn, executed_sql, (f"%{best}%",))
                        if not df.empty:
                            answer = f"📋 Không tìm chính xác '{cust_name}', nhưng tìm thấy hợp đồng cho gần khớp: '{best}':"
                        else:
                            answer = f"Không tìm thấy hợp đồng nào cho khách '{cust_name}'"
                    else:
                        answer = f"Không tìm thấy hợp đồng nào cho khách '{cust_name}'"
                else:
                    answer = f"📋 Hợp đồng / slot của khách '{cust_name}':"
            else:
                executed_sql = "SELECT contract_id, customer_name, slot_key, start_date, end_date FROM dim_customer_contract LIMIT 10"
                df = fetch_df(conn, executed_sql)
                answer = "Danh sách mẫu hợp đồng (không có tên cụ thể được trích xuất):"

        # Viết tin nhắn Zalo báo giá ngắn gọn
        elif any(k in q_lower for k in ["viết cho tôi một đoạn tin nhắn zalo", "tin nhắn zalo", "zalo"]):
            # try to detect unit type and months
            months = parse_months_from_text(q_lower)
            unit_type = None
            for ut in ["locker", "mini", "medium", "large"]:
                if ut in q_lower:
                    unit_type = ut.capitalize()
                    break
            if not unit_type:
                unit_type = "Mini"
            pricing = compute_pricing(conn, unit_type, months)
            zalo = generate_zalo_message(unit_type, pricing)
            executed_sql = "-- generated Zalo message using avg prices"
            answer = zalo

        # Lọc Slot trống
        elif (
            "trống" in q_lower
            or "còn trống" in q_lower
            or "slot trống" in q_lower
        ):
            conditions = ["(f.is_empty = 1 OR f.is_empty IS NULL)"]
            params = []

            if district_search:
                conditions.append("s.district ILIKE %s")
                params.append(f"%{district_search}%")
            if unit_type:
                conditions.append("s.unit_type = %s")
                params.append(unit_type)

            where_clause = " AND ".join(conditions)
            executed_sql = f"""
            SELECT s.slot_id, s.slot_code, s.unit_type, s.volume_cbm, s.slot_area_sqm, s.warehouse_name, s.district 
            FROM dim_warehouse_slot s 
            LEFT JOIN fact_inventory_snapshot f ON s.slot_key = f.slot_key AND f.snapshot_date = CURRENT_DATE 
            WHERE {where_clause}
            ORDER BY s.district, s.unit_type
            """
            df = fetch_df(conn, executed_sql, tuple(params) if params else None)

            filter_desc = []
            if district_search:
                filter_desc.append(f"khu vực {district_search}")
            if unit_type:
                filter_desc.append(f"loại {unit_type}")
            desc_str = (
                " (" + ", ".join(filter_desc) + ")" if filter_desc else ""
            )

            answer = f"📦 Tìm thấy **{len(df)}** slot còn trống{desc_str}:"

        # Lọc kho tổng quát
        elif (
            district_search
            or unit_type
            or "kho" in q_lower
            or "warehouse" in q_lower
        ):
            conditions = []
            params = []

            if district_search:
                conditions.append("s.district ILIKE %s")
                params.append(f"%{district_search}%")
            if unit_type:
                conditions.append("s.unit_type = %s")
                params.append(unit_type)

            where_clause = (
                " WHERE " + " AND ".join(conditions) if conditions else ""
            )
            executed_sql = f"""
            SELECT s.slot_id, s.slot_code, s.unit_type, s.volume_cbm, s.slot_area_sqm, s.storage_environment, s.warehouse_name, s.district 
            FROM dim_warehouse_slot s 
            {where_clause}
            ORDER BY s.district, s.unit_type
            LIMIT 100
            """
            df = fetch_df(conn, executed_sql, tuple(params) if params else None)
            answer = f"📋 Danh sách các ô kho (Tìm thấy {len(df)} kết quả):"

        # Mặc định
        else:
            executed_sql = """
            SELECT warehouse_name, district, COUNT(*) as total_slots 
            FROM dim_warehouse_slot 
            GROUP BY warehouse_name, district 
            ORDER BY warehouse_name
            """
            df = fetch_df(conn, executed_sql)
            answer = "📊 Thống kê tổng số lượng slot phân theo kho và quận:"

    except Exception as e:
        executed_sql = getattr(e, "query", str(e))
        answer = "Lỗi khi truy vấn DB: " + str(e)

    return executed_sql, df, answer


def chat_ui(conn):
    _, col_chat, _ = st.columns([1, 8, 1])

    with col_chat:
        # Simple CSS to tighten chat width and emulate chat-style look
        st.markdown(
            """
        <style>
        .chat-container .stMarkdown, .chat-container .stExpander {
            max-width: 900px;
            margin-left: auto;
            margin-right: auto;
        }
        .suggestions {margin: 8px 0px 12px 0px}
        </style>
        """,
            unsafe_allow_html=True,
        )

        st.subheader("🤖 AI Sales & Inventory Assistant")

        # Top controls: persona / quick model selector (visual only for now)
        with st.container():
            c1, c2 = st.columns([3, 1])
            with c1:
                st.caption("Chat-like AI assistant — quick prompts below")
            with c2:
                st.write(":sparkles:")

        # Suggested prompts (quick buttons) to feel like Gemini
        suggestion_prompts = [
            "Ở Quận 2 hiện tại còn slot kho máy lạnh nào trống không?",
            "Giá thuê trung bình của kho Mini là bao nhiêu?",
            "Top 10 khách hàng có doanh thu / MRR cao nhất",
            "Cho tôi danh sách các hợp đồng sắp hết hạn trong tháng này",
        ]

        st.markdown("<div class='suggestions'>Gợi ý nhanh: </div>", unsafe_allow_html=True)
        sp_cols = st.columns(len(suggestion_prompts))
        for i, p in enumerate(suggestion_prompts):
            if sp_cols[i].button(p, key=f"suggest_{i}"):
                # run the prompt immediately
                st.session_state.setdefault("chat_history", []).append({"role": "user", "text": p})
                executed_sql, df, answer = run_chat_query_db(conn, p)
                st.session_state["chat_history"].append({"role": "assistant", "sql": executed_sql, "answer": answer, "df": df})

        if "chat_history" not in st.session_state:
            st.session_state["chat_history"] = []

        # Render chat messages
        for msg in st.session_state["chat_history"]:
            with st.chat_message(msg["role"]):
                if msg["role"] == "user":
                    st.write(msg["text"])
                else:
                    st.write(msg["answer"])
                    if msg.get("df") is not None and not msg.get("df").empty:
                        st.dataframe(msg["df"], width='stretch')
                    if msg.get("sql"):
                        with st.expander("🔍 Executed SQL Query"):
                            st.code(msg.get("sql", ""), language="sql")

        # Input area
        if user_input := st.chat_input(
            "Hỏi AI (ví dụ: giá, chỗ trống, hợp đồng, vas...)"
        ):
            st.session_state.setdefault("chat_history", []).append({"role": "user", "text": user_input})
            with st.chat_message("user"):
                st.write(user_input)

            executed_sql, df, answer = run_chat_query_db(conn, user_input)

            with st.chat_message("assistant"):
                st.write(answer)
                if not df.empty:
                    st.dataframe(df, width='stretch')
                if executed_sql:
                    with st.expander("🔍 Executed SQL Query"):
                        st.code(executed_sql, language="sql")

            st.session_state["chat_history"].append({
                "role": "assistant",
                "sql": executed_sql,
                "answer": answer,
                "df": df,
            })


# -----------------------------------------------------------------------------
# 4. MAIN APP ENTRY POINT
# -----------------------------------------------------------------------------
def main():
    # 1. Kết nối & Khởi tạo DB
    try:
        conn, conn_ctx = connect_db_or_die()
    except Exception:
        return

    try:
        ensure_tables(conn, schema_path="schema.sql")
    except Exception as e:
        st.error("Khởi tạo schema thất bại")
        st.exception(e)
        conn_ctx.__exit__(None, None, None)
        return

    try:
        seed_database(conn)
    except Exception:
        pass

    # 2. Sidebar Navigation
    st.sidebar.title("📦 MyStorage Control Center")
    st.sidebar.markdown("---")

    view_mode = st.sidebar.radio(
        "Chọn Chức Năng:",
        [
            "📊 Executive Dashboard",
            "💬 AI Assistant Chat",
        ],
    )

    if view_mode == "📊 Executive Dashboard":
        st.sidebar.markdown("---")
        department = st.sidebar.radio(
            "Chọn Phòng Ban / Góc Nhìn:",
            [
                "💼 Phòng Sale (Kinh Doanh)",
                "🏗️ Phòng Vận Hành (Operations)",
                "📢 Phòng Marketing",
            ],
        )

    st.sidebar.markdown("---")
    st.sidebar.caption(
        "💡 *Dữ liệu được cập nhật tự động từ Data Warehouse PostgreSQL.*"
    )

    # 3. Main Display Logic
    if view_mode == "💬 AI Assistant Chat":
        st.title("💬 MyStorage AI Assistant Chatbot")
        st.markdown("---")
        chat_ui(conn)

    else:
        st.title("🚀 Executive & Operational Dashboard")
        st.markdown("---")

        # -----------------------------------------------------------------------------
        # DASHBOARD PHÒNG SALE
        # -----------------------------------------------------------------------------
        if department == "💼 Phòng Sale (Kinh Doanh)":
            st.header("💼 Báo Cáo Phân Tích Dành Cho Phòng Sale")

            # KPIs
            col1, col2, col3, col4 = st.columns(4)
            df_kpi_contract = run_query(
                conn,
                "SELECT COUNT(*) as total_active, SUM(rent_unit_price) as mrr FROM dim_customer_contract WHERE is_active = TRUE",
            )
            df_kpi_vas = run_query(
                conn, "SELECT SUM(total_amount) as vas_revenue FROM fact_vas_charge"
            )

            mrr = (
                df_kpi_contract["mrr"].iloc[0]
                if not df_kpi_contract.empty
                and pd.notnull(df_kpi_contract["mrr"].iloc[0])
                else 0
            )
            total_active = (
                df_kpi_contract["total_active"].iloc[0]
                if not df_kpi_contract.empty
                else 0
            )
            vas_rev = (
                df_kpi_vas["vas_revenue"].iloc[0]
                if not df_kpi_vas.empty
                and pd.notnull(df_kpi_vas["vas_revenue"].iloc[0])
                else 0
            )

            col1.metric("Doanh Thu Thuê Định Kỳ (MRR)", f"{mrr:,.0f} VNĐ")
            col2.metric("Doanh Thu Dịch Vụ (VAS)", f"{vas_rev:,.0f} VNĐ")
            col3.metric("Tổng Doanh Thu Ước Tính", f"{(mrr + vas_rev):,.0f} VNĐ")
            col4.metric("Số Hợp Đồng Đang Mở", f"{total_active} HĐ")

            st.markdown("---")

            # Cảnh báo Hợp đồng Sắp Hết Hạn
            st.subheader(
                "⚠️ Cảnh Báo: Hợp Đồng Sắp Hết Hạn (Cần Chăm Sóc Gia Hạn)"
            )
            query_expiry = """
                SELECT contract_id, customer_name, customer_type, industry, end_date, rent_unit_price
                FROM dim_customer_contract
                WHERE is_active = TRUE AND end_date >= CURRENT_DATE 
                ORDER BY end_date ASC
                LIMIT 10
            """
            df_expiry = run_query(conn, query_expiry)
            st.dataframe(df_expiry, width='stretch')

            # Biểu đồ Doanh thu & Cơ cấu Khách hàng
            c1, c2 = st.columns(2)

            with c1:
                st.subheader("Cơ Cấu Doanh Thu Theo Loại Khách Hàng")
                query_cust_type = """
                    SELECT customer_type, SUM(rent_unit_price) as total_revenue
                    FROM dim_customer_contract
                    GROUP BY customer_type
                """
                df_cust_type = run_query(conn, query_cust_type)
                if not df_cust_type.empty:
                    fig_cust = px.pie(
                        df_cust_type,
                        values="total_revenue",
                        names="customer_type",
                        hole=0.4,
                        color_discrete_sequence=px.colors.qualitative.Pastel,
                    )
                    st.plotly_chart(fig_cust, width='stretch')

            with c2:
                st.subheader("Top Ngành Hàng Đóng Góp Doanh Thu Cao Nhất")
                query_ind = """
                    SELECT industry, SUM(rent_unit_price) as total_revenue
                    FROM dim_customer_contract
                    GROUP BY industry
                    ORDER BY total_revenue DESC
                """
                df_ind = run_query(conn, query_ind)
                if not df_ind.empty:
                    fig_ind = px.bar(
                        df_ind,
                        x="total_revenue",
                        y="industry",
                        orientation="h",
                        labels={
                            "total_revenue": "Doanh thu (VNĐ)",
                            "industry": "Ngành hàng",
                        },
                        color="total_revenue",
                        color_continuous_scale="Viridis",
                    )
                    st.plotly_chart(fig_ind, width='stretch')

            # Cơ hội Upsell Dịch vụ VAS
            st.subheader(
                "💡 Khách Hàng Sử Dụng Dịch Vụ Gia Tăng (VAS) Nhiều Nhất -> Cơ Hội Cross-Sell"
            )
            query_top_vas = """
                SELECT c.customer_name, c.customer_type, v.service_type, SUM(v.total_amount) as total_vas
                FROM fact_vas_charge v
                JOIN dim_customer_contract c ON v.contract_key = c.contract_key
                GROUP BY c.customer_name, c.customer_type, v.service_type
                ORDER BY total_vas DESC
                LIMIT 10
            """
            df_top_vas = run_query(conn, query_top_vas)
            if not df_top_vas.empty:
                fig_vas_bar = px.bar(
                    df_top_vas,
                    x="customer_name",
                    y="total_vas",
                    color="service_type",
                    barmode="stack",
                    labels={
                        "total_vas": "Chi phí VAS (VNĐ)",
                        "customer_name": "Khách hàng",
                    },
                )
                st.plotly_chart(fig_vas_bar, width='stretch')

        # -----------------------------------------------------------------------------
        # DASHBOARD PHÒNG VẬN HÀNH (OPERATIONS)
        # -----------------------------------------------------------------------------
        elif department == "🏗️ Phòng Vận Hành (Operations)":
            st.header("🏗️ Báo Cáo Quản Lý Vận Hành & Sức Chứa Kho")

            # KPIs Vận hành
            col1, col2, col3, col4 = st.columns(4)
            df_occ = run_query(
                conn,
                "SELECT COUNT(*) as total_slots, SUM(CASE WHEN is_empty = 0 THEN 1 ELSE 0 END) as occupied_slots FROM fact_inventory_snapshot WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM fact_inventory_snapshot)",
            )
            df_tx_today = run_query(
                conn, "SELECT COUNT(*) as total_tx FROM fact_warehouse_transaction"
            )

            total_s = (
                df_occ["total_slots"].iloc[0]
                if not df_occ.empty and df_occ["total_slots"].iloc[0]
                else 1
            )
            occ_s = (
                df_occ["occupied_slots"].iloc[0]
                if not df_occ.empty and df_occ["occupied_slots"].iloc[0]
                else 0
            )
            occ_rate = (occ_s / total_s) * 100 if total_s > 0 else 0
            total_tx = (
                df_tx_today["total_tx"].iloc[0] if not df_tx_today.empty else 0
            )

            col1.metric("Tổng Số Slot Hệ Thống", f"{total_s:,}")
            col2.metric("Số Slot Đang Có Hàng", f"{occ_s:,}")
            col3.metric("Tỷ Lệ Lấp Đầy (Occupancy)", f"{occ_rate:.1f}%")
            col4.metric("Tổng Luồng Giao Dịch Kho", f"{total_tx:,} lượt")

            st.markdown("---")

            # Biểu đồ Tỷ lệ Lấp đầy theo Kho / Slot
            c1, c2 = st.columns(2)

            with c1:
                st.subheader("Tỷ Lệ Lấp Đầy (Occupancy Rate) Theo Từng Kho")
                query_wh_occ = """
                    SELECT w.warehouse_name, 
                           COUNT(*) as total_slots,
                           SUM(CASE WHEN s.is_empty = 0 THEN 1 ELSE 0 END) as occupied_slots,
                           ROUND((SUM(CASE WHEN s.is_empty = 0 THEN 1.0 ELSE 0.0 END) / COUNT(*)) * 100, 2) as occupancy_rate
                    FROM fact_inventory_snapshot s
                    JOIN dim_warehouse_slot w ON s.slot_key = w.slot_key
                    WHERE s.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_inventory_snapshot)
                    GROUP BY w.warehouse_name
                    ORDER BY occupancy_rate DESC
                """
                df_wh_occ = run_query(conn, query_wh_occ)
                if not df_wh_occ.empty:
                    fig_wh_occ = px.bar(
                        df_wh_occ,
                        x="warehouse_name",
                        y="occupancy_rate",
                        text="occupancy_rate",
                        color="occupancy_rate",
                        color_continuous_scale="Reds",
                        labels={
                            "occupancy_rate": "Tỷ lệ lấp đầy (%)",
                            "warehouse_name": "Tên Kho",
                        },
                    )
                    st.plotly_chart(fig_wh_occ, width='stretch')

            with c2:
                st.subheader("Phân Bố Loại Slot Kho (Unit Type) Đang Sử Dụng")
                query_unit_type = """
                    SELECT w.unit_type, COUNT(*) as count
                    FROM fact_inventory_snapshot s
                    JOIN dim_warehouse_slot w ON s.slot_key = w.slot_key
                    WHERE s.is_empty = 0 AND s.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_inventory_snapshot)
                    GROUP BY w.unit_type
                """
                df_unit_type = run_query(conn, query_unit_type)
                if not df_unit_type.empty:
                    fig_unit = px.pie(
                        df_unit_type, values="count", names="unit_type", hole=0.3
                    )
                    st.plotly_chart(fig_unit, width='stretch')

            # Tải trọng Giao dịch Nhập/Xuất Kho
            st.subheader(
                "📊 Tải Trọng Giao Dịch Nhập/Xuất Kho Theo Khung Giờ (Điều Phối Nhân Lực)"
            )
            query_tx_hour = """
                SELECT EXTRACT(HOUR FROM transaction_time) as hour, transaction_type, COUNT(*) as total_tx
                FROM fact_warehouse_transaction
                GROUP BY hour, transaction_type
                ORDER BY hour
            """
            df_tx_hour = run_query(conn, query_tx_hour)
            if not df_tx_hour.empty:
                fig_tx_hour = px.line(
                    df_tx_hour,
                    x="hour",
                    y="total_tx",
                    color="transaction_type",
                    markers=True,
                    labels={
                        "hour": "Khung giờ trong ngày (h)",
                        "total_tx": "Số lượt giao dịch",
                    },
                )
                st.plotly_chart(fig_tx_hour, width='stretch')

        # -----------------------------------------------------------------------------
        # DASHBOARD PHÒNG MARKETING
        # -----------------------------------------------------------------------------
        elif department == "📢 Phòng Marketing":
            st.header("📢 Báo Cáo Nghiên Cứu Thị Trường & Chiến Dịch Marketing")

            # Nhu cầu theo Địa lý
            st.subheader(
                "📍 Nhu Cầu Thuê Kho Theo Khu Vực / Quận (Geo-Targeting Ads)"
            )
            query_geo = """
                SELECT district, COUNT(*) as occupied_slots
                FROM fact_inventory_snapshot s
                JOIN dim_warehouse_slot w ON s.slot_key = w.slot_key
                WHERE s.is_empty = 0 AND s.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_inventory_snapshot)
                GROUP BY district
                ORDER BY occupied_slots DESC
            """
            df_geo = run_query(conn, query_geo)


# -----------------------------------------------------------------------------
# 2. QUẢN LÝ KẾT NỐI DATABASE
# -----------------------------------------------------------------------------
def connect_db_or_die():
    try:
        conn_ctx = get_conn()
        conn = conn_ctx.__enter__()
        return conn, conn_ctx
    except Exception as e:
        st.error(
            "Cannot connect to database. Check environment variables and DB status."
        )
        st.exception(e)
        raise


def run_query(conn, query: str) -> pd.DataFrame:
    try:
        df = fetch_df(conn, query)
        return df
    except Exception as e:
        st.error(f"Lỗi truy vấn Database: {e}")
        return pd.DataFrame()


# -----------------------------------------------------------------------------
# 3. AI CHATBOT QUERY LOGIC (XỬ LÝ 100% KỊCH BẢN)
# -----------------------------------------------------------------------------
def run_chat_query_db(conn, user_query: str):
    q_lower = user_query.lower()
    executed_sql = ""
    df = pd.DataFrame()
    answer = ""

    try:
        # 1. Trích xuất Loại kho (Unit Type)
        unit_type = None
        for ut in ["locker", "mini", "medium", "large"]:
            if ut in q_lower:
                unit_type = ut.capitalize()
                break

        # 2. Trích xuất Quận từ câu hỏi
        district_search = None
        q_match = re.search(
            r"(quận\s*\d+|q\d+|tân bình|bình thạnh|thủ đức|gò vấp|phú nhuận|tân phú|bình tân|quận 7|quận 2|quận 9)",
            q_lower,
        )
        if q_match:
            raw_district = q_match.group(0)
            if raw_district.startswith("q") and raw_district[1:].isdigit():
                district_search = f"QUẬN {raw_district[1:]}"
            else:
                district_search = raw_district.upper()

        # ----------------------------------------------------
        # PHÂN TÍCH VÀ ĐIỀU HƯỚNG CÂU HỎI CHATBOT
        # ----------------------------------------------------

        # Phân tích Doanh thu & Top Khách hàng
        if any(
            k in q_lower
            for k in [
                "khách hàng nào mang lại doanh thu cao nhất",
                "doanh thu cao nhất",
                "hợp đồng lớn nhất",
                "top khách hàng",
                "doanh thu hợp đồng",
                "khách hàng chi tiêu nhiều",
            ]
        ):
            executed_sql = """
            SELECT customer_name, customer_type, industry, rent_unit_price as mrr_vnd, start_date, end_date
            FROM dim_customer_contract
            ORDER BY rent_unit_price DESC
            LIMIT 10
            """
            df = fetch_df(conn, executed_sql)
            answer = "🏆 **Top 10 Khách hàng & Hợp đồng mang lại Doanh thu / MRR cao nhất:**"

        # Phân tích Ngành nghề / Lĩnh vực
        elif any(
            k in q_lower
            for k in [
                "ngành nghề nào thuê kho nhiều nhất",
                "lĩnh vực nào chiếm doanh thu",
                "ngành nghề",
                "khách hàng theo ngành",
                "lĩnh vực thuê",
            ]
        ):
            executed_sql = """
            SELECT industry as nganh_nghe, 
                   COUNT(contract_id) as so_luong_hop_dong, 
                   SUM(rent_unit_price) as tong_doanh_thu_mrr,
                   ROUND(AVG(rent_unit_price)::numeric, 0) as gia_trung_binh
            FROM dim_customer_contract
            GROUP BY industry
            ORDER BY tong_doanh_thu_mrr DESC
            """
            df = fetch_df(conn, executed_sql)
            answer = "📊 **Thống kê Doanh thu & Tỷ lệ thuê kho phân theo Ngành nghề / Lĩnh vực:**"

        # Phân loại Khách hàng (SME / Cá nhân / Enterprise)
        elif any(
            k in q_lower
            for k in [
                "bao nhiêu hợp đồng doanh nghiệp",
                "loại hình khách hàng",
                "sme",
                "cá nhân",
                "phân loại khách hàng",
            ]
        ):
            executed_sql = """
            SELECT customer_type as loai_khach_hang, 
                   COUNT(contract_id) as so_hop_dong, 
                   SUM(rent_unit_price) as tong_mrr_vnd,
                   ROUND(AVG(rent_unit_price)::numeric, 0) as gia_trung_binh_vnd
            FROM dim_customer_contract
            GROUP BY customer_type
            ORDER BY tong_mrr_vnd DESC
            """
            df = fetch_df(conn, executed_sql)
            answer = "👥 **Thống kê Cơ cấu Hợp đồng theo Loại hình Khách hàng:**"

        # Cảnh báo Hợp đồng sắp hết hạn
        elif any(
            k in q_lower
            for k in [
                "sắp hết hạn",
                "hợp đồng sắp hết hạn",
                "gia hạn hợp đồng",
                "hết hạn thuê",
            ]
        ):
            executed_sql = """
            SELECT contract_id, customer_name, customer_type, end_date, rent_unit_price, is_active
            FROM dim_customer_contract
            WHERE end_date >= CURRENT_DATE
            ORDER BY end_date ASC
            LIMIT 10
            """
            df = fetch_df(conn, executed_sql)
            answer = "⚠️ **Danh sách Hợp đồng sắp hết hạn gần nhất (Cần ưu tiên gia hạn):**"

        # Phân tích Dịch vụ VAS
        elif any(
            k in q_lower
            for k in [
                "vas",
                "dịch vụ gia tăng",
                "đóng gói",
                "vận chuyển",
                "doanh thu vas",
                "dịch vụ vas",
            ]
        ):
            executed_sql = """
            SELECT service_type as loai_dich_vu, 
                   COUNT(*) as so_luot_su_dung, 
                   SUM(total_amount) as tong_doanh_thu_vas_vnd,
                   ROUND(AVG(total_amount)::numeric, 0) as chi_phi_trung_binh
            FROM fact_vas_charge
            GROUP BY service_type
            ORDER BY tong_doanh_thu_vas_vnd DESC
            """
            df = fetch_df(conn, executed_sql)
            answer = "🚚 **Thống kê Doanh thu & Tần suất sử dụng Dịch vụ Gia tăng (VAS):**"

        # Giao dịch Nhập / Xuất kho
        elif any(
            k in q_lower
            for k in [
                "nhập xuất",
                "giao dịch",
                "tần suất giao dịch",
                "luân chuyển",
                "xuất nhập nhiều nhất",
            ]
        ):
            executed_sql = """
            SELECT s.warehouse_name, 
                   t.transaction_type as loai_giao_dich, 
                   COUNT(*) as tong_so_giao_dich
            FROM fact_warehouse_transaction t
            JOIN dim_warehouse_slot s ON t.slot_key = s.slot_key
            GROUP BY s.warehouse_name, t.transaction_type
            ORDER BY s.warehouse_name, tong_so_giao_dich DESC
            """
            df = fetch_df(conn, executed_sql)
            answer = "🔄 **Thống kê Tần suất Giao dịch Nhập / Xuất theo từng Kho:**"

        # Phân tích SKU / Danh mục hàng hóa
        elif any(
            k in q_lower
            for k in [
                "sku",
                "mặt hàng nào lưu kho nhiều nhất",
                "danh mục hàng hóa",
                "hàng hóa",
                "mặt hàng",
                "danh mục",
            ]
        ):
            executed_sql = """
            SELECT i.item_category as danh_muc_hang, 
                   COUNT(f.snapshot_key) as so_luong_slot_dang_chua,
                   COUNT(DISTINCT i.item_key) as so_sku_khac_nhau
            FROM fact_inventory_snapshot f
            JOIN dim_item i ON f.item_key = i.item_key
            WHERE f.is_empty = 0
            GROUP BY i.item_category
            ORDER BY so_luong_slot_dang_chua DESC
            """
            df = fetch_df(conn, executed_sql)
            answer = "📦 **Thống kê Danh mục Hàng hóa & SKU đang lưu kho:**"

        # Kho Máy lạnh / Điều hòa
        elif any(
            k in q_lower
            for k in [
                "máy lạnh",
                "điều hòa",
                "nhiệt độ chuẩn",
                "air-conditioned",
                "môi trường",
                "máy lạnh còn trống",
            ]
        ):
            executed_sql = """
            SELECT s.warehouse_name, s.district, s.storage_environment,
                   COUNT(s.slot_id) as tong_slot,
                   SUM(CASE WHEN f.is_empty = 1 OR f.is_empty IS NULL THEN 1 ELSE 0 END) as slot_trong
            FROM dim_warehouse_slot s
            LEFT JOIN fact_inventory_snapshot f ON s.slot_key = f.slot_key AND f.snapshot_date = CURRENT_DATE
            WHERE s.storage_environment ILIKE '%%Air%%' OR s.storage_environment ILIKE '%%Cold%%' OR s.storage_environment ILIKE '%%Lạnh%%'
            GROUP BY s.warehouse_name, s.district, s.storage_environment
            ORDER BY slot_trong DESC
            """
            df = fetch_df(conn, executed_sql)
            answer = "❄️ **Tình trạng sức chứa các Kho Điều Hòa / Máy Lạnh (Air-Conditioned / Climate Controlled):**"

        # Tỷ lệ Lấp đầy (Occupancy Rate)
        elif any(
            k in q_lower
            for k in [
                "tỷ lệ lấp đầy",
                "kho nào đầy nhất",
                "hiệu suất kho",
                "occupancy",
                "lấp đầy",
            ]
        ):
            executed_sql = """
            SELECT s.warehouse_name, s.district,
                   COUNT(s.slot_id) as tong_slot,
                   SUM(CASE WHEN f.is_empty = 0 THEN 1 ELSE 0 END) as slot_da_thue,
                   ROUND((SUM(CASE WHEN f.is_empty = 0 THEN 1.0 ELSE 0.0 END) / COUNT(s.slot_id) * 100)::numeric, 1) as ty_le_lap_day_pct
            FROM dim_warehouse_slot s
            LEFT JOIN fact_inventory_snapshot f ON s.slot_key = f.slot_key AND f.snapshot_date = CURRENT_DATE
            GROUP BY s.warehouse_name, s.district
            ORDER BY ty_le_lap_day_pct DESC
            """
            df = fetch_df(conn, executed_sql)
            answer = "📈 **Báo cáo Tỷ lệ Lấp đầy (Occupancy Rate) chi tiết từng Chi nhánh Kho:**"

        # Kho còn trống nhiều nhất
        elif any(
            k in q_lower
            for k in [
                "chỗ nào còn",
                "kho nào còn",
                "trống nhiều nhất",
                "còn nhiều kho",
                "ở đâu còn",
                "kho nào sẵn sàng",
            ]
        ):
            executed_sql = """
            SELECT s.warehouse_name, s.district, 
                   COUNT(s.slot_id) as tong_so_slot,
                   SUM(CASE WHEN f.is_empty = 1 OR f.is_empty IS NULL THEN 1 ELSE 0 END) as so_slot_con_trong
            FROM dim_warehouse_slot s
            LEFT JOIN fact_inventory_snapshot f ON s.slot_key = f.slot_key AND f.snapshot_date = CURRENT_DATE
            GROUP BY s.warehouse_name, s.district
            ORDER BY so_slot_con_trong DESC
            """
            df = fetch_df(conn, executed_sql)
            answer = "🔥 **Thống kê các địa điểm kho còn nhiều slot trống nhất hiện tại:**"

        # Vị trí / Địa chỉ kho gần nhất
        elif any(
            k in q_lower
            for k in [
                "gần nhất",
                "ở đâu",
                "địa chỉ",
                "vị trí kho",
                "chi nhánh",
                "kho gần",
            ]
        ):
            if district_search:
                executed_sql = """
                SELECT s.warehouse_name, s.district, COUNT(s.slot_id) as tong_slot,
                       SUM(CASE WHEN f.is_empty = 1 OR f.is_empty IS NULL THEN 1 ELSE 0 END) as slot_trong
                FROM dim_warehouse_slot s
                LEFT JOIN fact_inventory_snapshot f ON s.slot_key = f.slot_key AND f.snapshot_date = CURRENT_DATE
                WHERE s.district ILIKE %s
                GROUP BY s.warehouse_name, s.district
                """
                df = fetch_df(conn, executed_sql, (f"%{district_search}%",))
                if not df.empty:
                    answer = f"📍 Tìm thấy **{len(df)}** chi nhánh kho phục vụ khu vực **{district_search}**:"
                else:
                    executed_sql_all = """
                    SELECT s.warehouse_name, s.district, COUNT(s.slot_id) as tong_slot,
                           SUM(CASE WHEN f.is_empty = 1 OR f.is_empty IS NULL THEN 1 ELSE 0 END) as slot_trong
                    FROM dim_warehouse_slot s
                    LEFT JOIN fact_inventory_snapshot f ON s.slot_key = f.slot_key AND f.snapshot_date = CURRENT_DATE
                    GROUP BY s.warehouse_name, s.district
                    """
                    df = fetch_df(conn, executed_sql_all)
                    answer = f"Hiện MyStorage chưa có kho trực tiếp tại **{district_search}**. Dưới đây là danh sách các chi nhánh kho gần bạn nhất:"
            else:
                executed_sql = """
                SELECT s.warehouse_name, s.district, COUNT(s.slot_id) as tong_slot,
                       SUM(CASE WHEN f.is_empty = 1 OR f.is_empty IS NULL THEN 1 ELSE 0 END) as slot_trong
                FROM dim_warehouse_slot s
                LEFT JOIN fact_inventory_snapshot f ON s.slot_key = f.slot_key AND f.snapshot_date = CURRENT_DATE
                GROUP BY s.warehouse_name, s.district
                ORDER BY s.district
                """
                df = fetch_df(conn, executed_sql)
                answer = "📍 **Danh sách các vị trí/chi nhánh kho của MyStorage:**"

        # Tra cứu Giá thuê
        elif "giá" in q_lower or "giá thuê" in q_lower:
            if unit_type:
                executed_sql = """
                SELECT s.unit_type, 
                       ROUND(AVG(c.rent_unit_price)::numeric, 0) as gia_trung_binh,
                       MIN(c.rent_unit_price) as gia_thap_nhat,
                       MAX(c.rent_unit_price) as gia_cao_nhat,
                       COUNT(c.contract_id) as so_hop_dong
                FROM dim_customer_contract c
                JOIN fact_inventory_snapshot f ON c.contract_key = f.contract_key
                JOIN dim_warehouse_slot s ON f.slot_key = s.slot_key
                WHERE s.unit_type = %s
                GROUP BY s.unit_type
                """
                df = fetch_df(conn, executed_sql, (unit_type,))
                answer = (
                    f"💰 Bảng giá thuê tham khảo cho loại kho **{unit_type}**:"
                )
            else:
                executed_sql = """
                SELECT s.unit_type, 
                       ROUND(AVG(c.rent_unit_price)::numeric, 0) as gia_trung_binh_vnd,
                       MIN(c.rent_unit_price) as gia_thap_nhat_vnd,
                       MAX(c.rent_unit_price) as gia_cao_nhat_vnd
                FROM dim_customer_contract c
                JOIN fact_inventory_snapshot f ON c.contract_key = f.contract_key
                JOIN dim_warehouse_slot s ON f.slot_key = s.slot_key
                GROUP BY s.unit_type
                ORDER BY gia_trung_binh_vnd DESC
                """
                df = fetch_df(conn, executed_sql)
                answer = "💰 Bảng giá thuê trung bình phân theo từng loại kho (Unit Type):"

        # Lọc Slot trống
        elif (
            "trống" in q_lower
            or "còn trống" in q_lower
            or "slot trống" in q_lower
        ):
            conditions = ["(f.is_empty = 1 OR f.is_empty IS NULL)"]
            params = []

            if district_search:
                conditions.append("s.district ILIKE %s")
                params.append(f"%{district_search}%")
            if unit_type:
                conditions.append("s.unit_type = %s")
                params.append(unit_type)

            where_clause = " AND ".join(conditions)
            executed_sql = f"""
            SELECT s.slot_id, s.slot_code, s.unit_type, s.volume_cbm, s.slot_area_sqm, s.warehouse_name, s.district 
            FROM dim_warehouse_slot s 
            LEFT JOIN fact_inventory_snapshot f ON s.slot_key = f.slot_key AND f.snapshot_date = CURRENT_DATE 
            WHERE {where_clause}
            ORDER BY s.district, s.unit_type
            """
            df = fetch_df(conn, executed_sql, tuple(params) if params else None)

            filter_desc = []
            if district_search:
                filter_desc.append(f"khu vực {district_search}")
            if unit_type:
                filter_desc.append(f"loại {unit_type}")
            desc_str = (
                " (" + ", ".join(filter_desc) + ")" if filter_desc else ""
            )

            answer = f"📦 Tìm thấy **{len(df)}** slot còn trống{desc_str}:"

        # Lọc kho tổng quát
        elif (
            district_search
            or unit_type
            or "kho" in q_lower
            or "warehouse" in q_lower
        ):
            conditions = []
            params = []

            if district_search:
                conditions.append("s.district ILIKE %s")
                params.append(f"%{district_search}%")
            if unit_type:
                conditions.append("s.unit_type = %s")
                params.append(unit_type)

            where_clause = (
                " WHERE " + " AND ".join(conditions) if conditions else ""
            )
            executed_sql = f"""
            SELECT s.slot_id, s.slot_code, s.unit_type, s.volume_cbm, s.slot_area_sqm, s.storage_environment, s.warehouse_name, s.district 
            FROM dim_warehouse_slot s 
            {where_clause}
            ORDER BY s.district, s.unit_type
            LIMIT 100
            """
            df = fetch_df(conn, executed_sql, tuple(params) if params else None)
            answer = f"📋 Danh sách các ô kho (Tìm thấy {len(df)} kết quả):"

        # Mặc định
        else:
            executed_sql = """
            SELECT warehouse_name, district, COUNT(*) as total_slots 
            FROM dim_warehouse_slot 
            GROUP BY warehouse_name, district 
            ORDER BY warehouse_name
            """
            df = fetch_df(conn, executed_sql)
            answer = "📊 Thống kê tổng số lượng slot phân theo kho và quận:"

    except Exception as e:
        executed_sql = getattr(e, "query", str(e))
        answer = "Lỗi khi truy vấn DB: " + str(e)

    return executed_sql, df, answer


def chat_ui(conn):
    _, col_chat, _ = st.columns([1, 8, 1])

    with col_chat:
        st.subheader("🤖 AI Sales & Inventory Assistant")

        if "chat_history" not in st.session_state:
            st.session_state["chat_history"] = []

        for msg in st.session_state["chat_history"]:
            with st.chat_message(msg["role"]):
                if msg["role"] == "user":
                    st.write(msg["text"])
                else:
                    st.write(msg["answer"])
                    if msg.get("df") is not None and not msg.get("df").empty:
                        st.dataframe(msg["df"], width='stretch')
                    if msg.get("sql"):
                        with st.expander("🔍 Executed SQL Query"):
                            st.code(msg.get("sql", ""), language="sql")

        if user_input := st.chat_input(
            "Hỏi AI (Ví dụ: khách hàng doanh thu cao nhất, hợp đồng sắp hết hạn, dịch vụ vas...)..."
        ):
            st.session_state["chat_history"].append(
                {"role": "user", "text": user_input}
            )
            with st.chat_message("user"):
                st.write(user_input)

            executed_sql, df, answer = run_chat_query_db(conn, user_input)

            with st.chat_message("assistant"):
                st.write(answer)
                if not df.empty:
                    st.dataframe(df, width='stretch')
                if executed_sql:
                    with st.expander("🔍 Executed SQL Query"):
                        st.code(executed_sql, language="sql")

            st.session_state["chat_history"].append(
                {
                    "role": "assistant",
                    "sql": executed_sql,
                    "answer": answer,
                    "df": df,
                }
            )
# Run
if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()