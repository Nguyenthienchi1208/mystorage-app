-- PostgreSQL DDL schema for MyStorage (matches provided DBML specification)

-- DIMENSIONS
CREATE TABLE IF NOT EXISTS dim_date (
    date_key INT PRIMARY KEY,
    full_date DATE NOT NULL,
    day_of_week INT,
    day_name VARCHAR(10),
    month INT,
    quarter INT,
    year INT,
    is_weekend BOOLEAN
);

CREATE TABLE IF NOT EXISTS dim_warehouse_slot (
    slot_key SERIAL PRIMARY KEY,
    slot_id VARCHAR(50) UNIQUE NOT NULL,
    slot_code VARCHAR(20) NOT NULL,
    unit_type VARCHAR(50),
    volume_cbm NUMERIC(10,2),
    slot_area_sqm NUMERIC(10,2),
    storage_environment VARCHAR(50),
    access_type VARCHAR(50),
    warehouse_id VARCHAR(50) NOT NULL,
    warehouse_name VARCHAR(100) NOT NULL,
    hub_type VARCHAR(50),
    address VARCHAR(255),
    district VARCHAR(50),
    city VARCHAR(50) DEFAULT 'TP. Hồ Chí Minh',
    latitude NUMERIC(10,7),
    longitude NUMERIC(10,7),
    has_coworking_space BOOLEAN DEFAULT true
);

CREATE TABLE IF NOT EXISTS dim_customer_contract (
    contract_key SERIAL PRIMARY KEY,
    contract_id VARCHAR(50) UNIQUE NOT NULL,
    customer_id VARCHAR(50) NOT NULL,
    customer_name VARCHAR(150) NOT NULL,
    customer_type VARCHAR(30),
    industry VARCHAR(50),
    rental_model VARCHAR(50),
    billing_cycle VARCHAR(20),
    rent_unit_price NUMERIC(12,2),
    deposit_amount NUMERIC(12,2),
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    is_active BOOLEAN DEFAULT true
);

CREATE TABLE IF NOT EXISTS dim_item (
    item_key SERIAL PRIMARY KEY,
    sku_code VARCHAR(50) UNIQUE NOT NULL,
    item_name VARCHAR(150) NOT NULL,
    item_category VARCHAR(50),
    unit VARCHAR(20),
    weight_kg NUMERIC(10,2),
    volume_cbm NUMERIC(10,2)
);

-- FACTS
CREATE TABLE IF NOT EXISTS fact_warehouse_transaction (
    transaction_id VARCHAR(50) PRIMARY KEY,
    date_key INT REFERENCES dim_date(date_key),
    transaction_time TIMESTAMP NOT NULL,
    slot_key INT REFERENCES dim_warehouse_slot(slot_key),
    contract_key INT REFERENCES dim_customer_contract(contract_key),
    item_key INT REFERENCES dim_item(item_key),
    transaction_type VARCHAR(30) NOT NULL,
    quantity INT NOT NULL,
    total_weight_kg NUMERIC(12,2),
    total_volume_cbm NUMERIC(12,2)
);

CREATE TABLE IF NOT EXISTS fact_vas_charge (
    charge_id VARCHAR(50) PRIMARY KEY,
    date_key INT REFERENCES dim_date(date_key),
    charge_date DATE NOT NULL,
    contract_key INT REFERENCES dim_customer_contract(contract_key),
    service_type VARCHAR(50) NOT NULL,
    quantity NUMERIC(10,2) NOT NULL,
    unit_price NUMERIC(12,2) NOT NULL,
    total_amount NUMERIC(14,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS fact_inventory_snapshot (
    snapshot_date DATE NOT NULL,
    date_key INT REFERENCES dim_date(date_key),
    slot_key INT REFERENCES dim_warehouse_slot(slot_key),
    contract_key INT REFERENCES dim_customer_contract(contract_key),
    item_key INT REFERENCES dim_item(item_key),
    slot_status VARCHAR(20) NOT NULL,
    is_empty INT NOT NULL,
    occupied_volume_cbm NUMERIC(10,2) DEFAULT 0,
    occupied_area_sqm NUMERIC(10,2) DEFAULT 0
);
