-- =====================================================================
-- Legal Metrology (Packaged Commodities) Compliance Checker
-- Database schema — PostgreSQL 14+
-- Built from: lmpc_rules_2011.json
--
-- Run order:
--   1. Run SECTION 0 from an admin connection (psql -U postgres)
--   2. Connect to the new database: \c lmpc_compliance
--   3. Run SECTIONS A, B, INDEXES, and C in this same file
-- =====================================================================

-- =====================================================================
-- SECTION 0 — CREATE DATABASE
-- Must be run outside a transaction block, from an admin connection.
-- =====================================================================
CREATE DATABASE lmpc_compliance;

-- After this, connect to it before continuing:
-- \c lmpc_compliance

CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- for gen_random_uuid()

-- =====================================================================
-- ENUM TYPES
-- =====================================================================
CREATE TYPE requirement_level AS ENUM ('mandatory', 'conditional');
CREATE TYPE condition_kind    AS ENUM ('exception', 'waiver');
CREATE TYPE font_table_type   AS ENUM ('by_weight_volume', 'by_panel_area');
CREATE TYPE exclusion_type    AS ENUM ('full_scope_exclusion', 'rule_26_full_exemption');
CREATE TYPE user_role         AS ENUM ('admin', 'enforcement_officer', 'viewer');
CREATE TYPE scan_status       AS ENUM ('pending', 'processing', 'completed', 'failed');
CREATE TYPE violation_severity AS ENUM ('critical', 'major', 'minor');

-- =====================================================================
-- SECTION A — RULE REFERENCE TABLES
-- Seeded once from lmpc_rules_2011.json (see SECTION C below).
-- The rule engine reads ONLY these tables — rule values are never
-- hardcoded in application code, so a rule amendment is a data update,
-- not a code deployment.
-- =====================================================================

-- One-row table holding source/version info from the JSON's top level
CREATE TABLE rule_metadata (
    id                    SMALLINT PRIMARY KEY DEFAULT 1,
    source                TEXT NOT NULL,
    effective_from        DATE NOT NULL,
    implementation_note   TEXT,
    loaded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT single_row CHECK (id = 1)
);

-- Maps 1:1 to the "mandatory_declarations" array in the JSON
CREATE TABLE declaration_types (
    id                  TEXT PRIMARY KEY,        -- e.g. 'mrp', 'net_quantity'
    rule_ref            TEXT NOT NULL,
    field_label         TEXT NOT NULL,
    requirement_level   requirement_level NOT NULL,
    format_note         TEXT,
    rounding_rule       TEXT,
    unit_rule           TEXT,
    notes               TEXT
);

-- Each declaration type's "exceptions" / "waiver_conditions" arrays
CREATE TABLE declaration_type_conditions (
    id                    SERIAL PRIMARY KEY,
    declaration_type_id   TEXT NOT NULL REFERENCES declaration_types(id) ON DELETE CASCADE,
    condition_kind        condition_kind NOT NULL,
    description           TEXT NOT NULL
);

-- Maps to "font_size_rules" top-level scalars (general_letter_height_mm etc.)
CREATE TABLE font_size_general_rules (
    id                          SMALLINT PRIMARY KEY DEFAULT 1,
    normal_letter_height_mm     NUMERIC(4,1) NOT NULL,
    molded_letter_height_mm     NUMERIC(4,1) NOT NULL,
    width_to_height_ratio_note  TEXT,
    applicability_exception     TEXT,
    CONSTRAINT single_row CHECK (id = 1)
);

-- Maps to "table_1_by_weight_or_volume" and "table_2_by_length_area_or_number"
-- Kept as real numeric ranges (not JSON) because the rule engine needs to
-- do "WHERE :value BETWEEN range_min AND range_max" lookups at check time.
CREATE TABLE font_size_thresholds (
    id                      SERIAL PRIMARY KEY,
    table_type              font_table_type NOT NULL,
    range_label             TEXT NOT NULL,
    range_min               NUMERIC(10,2),
    range_max               NUMERIC(10,2),      -- NULL = open-ended ("above X")
    range_unit               TEXT NOT NULL,       -- 'g_ml' or 'cm2'
    min_height_mm_normal    NUMERIC(4,1) NOT NULL,
    min_height_mm_molded    NUMERIC(4,1) NOT NULL
);

-- Maps to "placement_and_manner_rules" top-level fields
CREATE TABLE placement_rules (
    id                                  SMALLINT PRIMARY KEY DEFAULT 1,
    rule_ref                            TEXT NOT NULL,
    principal_display_panel_required    BOOLEAN NOT NULL DEFAULT true,
    clear_space_above_below             TEXT,
    clear_space_left_right              TEXT,
    CONSTRAINT single_row CHECK (id = 1)
);

-- Maps to "placement_and_manner_rules.manner" array
CREATE TABLE placement_manner_rules (
    id            SERIAL PRIMARY KEY,
    description   TEXT NOT NULL
);

-- Maps to "prohibited_practices" array
CREATE TABLE prohibited_practices (
    id               SERIAL PRIMARY KEY,
    description      TEXT NOT NULL,
    exception_note   TEXT
);

-- Maps to "scope_exclusions" (both rule_3 and rule_26 lists)
CREATE TABLE scope_exclusions (
    id               SERIAL PRIMARY KEY,
    exclusion_type   exclusion_type NOT NULL,
    condition_text   TEXT NOT NULL,
    note             TEXT
);

-- Maps to "e_commerce_declarations.required_fields"
CREATE TABLE ecommerce_declaration_fields (
    id           SERIAL PRIMARY KEY,
    field_name   TEXT NOT NULL
);

-- Maps to "wholesale_package_declarations.required_fields"
CREATE TABLE wholesale_declaration_fields (
    id           SERIAL PRIMARY KEY,
    field_name   TEXT NOT NULL
);

-- Maps to "penalties"
CREATE TABLE penalties (
    id                    SERIAL PRIMARY KEY,
    rule_ref              TEXT NOT NULL,
    violation_category    TEXT NOT NULL,
    fine_amount_inr       NUMERIC(10,2) NOT NULL
);

-- =====================================================================
-- SECTION B — OPERATIONAL TABLES
-- Runtime data generated as the app is used (uploads, scans, results).
-- =====================================================================

CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    role            user_role NOT NULL DEFAULT 'viewer',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE categories (
    id                                SERIAL PRIMARY KEY,
    name                              TEXT NOT NULL UNIQUE,  -- 'food', 'cosmetics', 'textile', 'electronics'...
    is_industrial_or_institutional    BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE products (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                  TEXT NOT NULL,
    category_id           INTEGER REFERENCES categories(id),
    is_imported           BOOLEAN NOT NULL DEFAULT false,
    sold_via_ecommerce    BOOLEAN NOT NULL DEFAULT false,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE scans (
    id                                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id                           UUID NOT NULL REFERENCES products(id),
    uploaded_by                          UUID NOT NULL REFERENCES users(id),
    image_path                           TEXT NOT NULL,
    principal_display_panel_area_cm2     NUMERIC(10,2),   -- needed for Rule 7 table_2 lookup
    net_quantity_value                   NUMERIC(10,2),   -- needed for Rule 7 table_1 lookup
    net_quantity_unit                    TEXT,
    status                               scan_status NOT NULL DEFAULT 'pending',
    scanned_at                           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE declarations (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_id                UUID NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    declaration_type_id    TEXT REFERENCES declaration_types(id),
    extracted_text         TEXT,
    font_height_mm         NUMERIC(4,1),
    is_molded              BOOLEAN NOT NULL DEFAULT false,   -- blown/formed/molded/embossed/perforated surface
    bbox                   JSONB,                            -- {x, y, width, height} on the source image
    confidence             NUMERIC(4,3),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE violations (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_id                UUID NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    declaration_id         UUID REFERENCES declarations(id),
    declaration_type_id    TEXT REFERENCES declaration_types(id),
    rule_ref               TEXT NOT NULL,
    severity               violation_severity NOT NULL,
    description            TEXT NOT NULL,
    detected_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE reports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_id         UUID NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    file_path       TEXT NOT NULL,
    format          TEXT NOT NULL,   -- 'pdf' or 'docx'
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
    id            BIGSERIAL PRIMARY KEY,
    user_id       UUID REFERENCES users(id),
    action        TEXT NOT NULL,
    entity_type   TEXT,
    entity_id     TEXT,
    logged_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =====================================================================
-- INDEXES
-- =====================================================================
CREATE INDEX idx_scans_product        ON scans(product_id);
CREATE INDEX idx_declarations_scan    ON declarations(scan_id);
CREATE INDEX idx_declarations_type    ON declarations(declaration_type_id);
CREATE INDEX idx_violations_scan      ON violations(scan_id);
CREATE INDEX idx_violations_type      ON violations(declaration_type_id);
CREATE INDEX idx_products_search      ON products USING gin (to_tsvector('english', name));
CREATE INDEX idx_font_thresholds_type ON font_size_thresholds(table_type);

-- =====================================================================
-- SECTION C — SEED DATA (directly transcribed from lmpc_rules_2011.json)
-- =====================================================================

INSERT INTO rule_metadata (source, effective_from, implementation_note) VALUES (
  'The Legal Metrology (Packaged Commodities) Rules, 2011 (as amended)',
  '2011-04-01',
  'Reflects 2021+ amendments: Rule 5 omitted, MRP wording simplified, country of origin and e-commerce fields added, USP thresholds refined. Cross-check against latest consolidated text for production use, since further amendments continue through 2025-2026.'
);

-- ---- mandatory_declarations ----
INSERT INTO declaration_types (id, rule_ref, field_label, requirement_level, format_note, rounding_rule, unit_rule, notes) VALUES
('manufacturer_packer_importer', 'Rule 6(1)(a), Rule 10', 'Name and complete address of manufacturer, or manufacturer AND packer (if different), or importer (if imported)', 'mandatory', NULL, NULL, NULL,
 'If no qualifying words ''manufactured by''/''packed by'' appear, name+address is presumed to be the manufacturer''s. For imported goods packed in India, must also show name/address of Indian packer/importer on principal display panel.'),
('generic_name', 'Rule 6(1)(b)', 'Common or generic name of the commodity', 'mandatory', NULL, NULL, NULL,
 'If package contains more than one product, name and quantity/number of each must be mentioned.'),
('net_quantity', 'Rule 6(1)(c), Rule 12, Rule 13', 'Net quantity in standard unit of weight/measure/number', 'mandatory', NULL, NULL, NULL,
 'Must use SI units only. No ''dozen/score/gross''. Cannot use words like ''minimum'', ''about'', ''approximately'' etc. that create misleading impression of quantity.'),
('mfg_month_year', 'Rule 6(1)(d)', 'Month and year of manufacture/pre-packing/import', 'mandatory', NULL, NULL, NULL, NULL),
('mrp', 'Rule 6(1)(e), Rule 2(m)', 'Retail Sale Price (MRP)', 'mandatory', 'Declared in Indian currency, inclusive of all taxes (rigid wording templates removed by amendment)', 'Fraction below 50 paise rounds down to preceding rupee; fraction from 50-95 paise rounds to 50 paise', NULL, NULL),
('dimensions', 'Rule 6(1)(f), Rule 14, Rule 15', 'Dimensions of commodity, where relevant to price (e.g. bedsheets, sarees, towels, fabric)', 'conditional', NULL, NULL, NULL,
 'Required only where size is relevant to the commodity or where dimensions/weight relate to price.'),
('country_of_origin', 'Amended provision (post-2021)', 'Country of origin / manufacture', 'conditional', NULL, NULL, NULL, 'Mandatory for imported products.'),
('consumer_care', 'Rule 6(2)', 'Name, address, telephone number, and email (if available) of consumer complaints contact', 'mandatory', NULL, NULL, NULL, NULL),
('unit_sale_price', 'Rule 2(m) / general practice (amended)', 'Unit sale price (USP)', 'mandatory', NULL, 'Rounded to nearest 2 decimal places',
 'If net quantity is less than 1 kg/litre, USP is declared per gram or per millilitre; if net quantity is 1 kg/litre or more, USP is declared per kilogram or per litre', NULL);

-- ---- per-declaration exceptions / waivers ----
INSERT INTO declaration_type_conditions (declaration_type_id, condition_kind, description) VALUES
('mfg_month_year', 'exception', 'Bidis or incense sticks'),
('mfg_month_year', 'exception', 'Domestic LPG cylinders (14.2kg or 5kg) marketed by public sector undertaking'),
('mfg_month_year', 'exception', 'Food articles (governed by food safety law instead)'),
('mfg_month_year', 'exception', 'Seeds certified under Seeds Act, 1966'),
('mfg_month_year', 'exception', 'Cosmetics (governed by Drugs and Cosmetics Rules, 1945)'),
('mrp', 'exception', 'Bidis'),
('mrp', 'exception', 'Domestic LPG cylinders priced under Administrative Price Mechanism'),
('mrp', 'exception', 'Alcoholic beverages/spirituous liquor (governed by State Excise laws unless those laws don''t cover MRP declaration)'),
('unit_sale_price', 'waiver', 'Not required if principal display panel area is 100 sq cm or less'),
('unit_sale_price', 'waiver', 'Not required if MRP is Rs. 35 or less');

-- ---- font_size_rules (general scalars) ----
INSERT INTO font_size_general_rules (normal_letter_height_mm, molded_letter_height_mm, width_to_height_ratio_note, applicability_exception) VALUES
(1, 2, 'Letter/numeral width must be >= 1/3 of its height (except numeral ''1'' and letters i, I, l)',
 'Rules 7(1)-(3) do not apply if the info is already required to be given under some other applicable law.');

-- ---- font_size_rules.table_1_by_weight_or_volume ----
INSERT INTO font_size_thresholds (table_type, range_label, range_min, range_max, range_unit, min_height_mm_normal, min_height_mm_molded) VALUES
('by_weight_volume', 'up to 200 g/ml', 0, 200, 'g_ml', 1, 2),
('by_weight_volume', 'above 200 g/ml up to 500 g/ml', 200, 500, 'g_ml', 2, 4),
('by_weight_volume', 'above 500 g/ml', 500, NULL, 'g_ml', 4, 6);

-- ---- font_size_rules.table_2_by_length_area_or_number ----
INSERT INTO font_size_thresholds (table_type, range_label, range_min, range_max, range_unit, min_height_mm_normal, min_height_mm_molded) VALUES
('by_panel_area', 'up to 100 cm2', 0, 100, 'cm2', 1, 2),
('by_panel_area', 'above 100 up to 500 cm2', 100, 500, 'cm2', 2, 4),
('by_panel_area', 'above 500 up to 2500 cm2', 500, 2500, 'cm2', 4, 6),
('by_panel_area', 'above 2500 cm2', 2500, NULL, 'cm2', 6, 6);

-- ---- placement_and_manner_rules ----
INSERT INTO placement_rules (rule_ref, principal_display_panel_required, clear_space_above_below, clear_space_left_right) VALUES
('Rule 8, Rule 9', true, 'at least equal to the height of the numeral used', 'at least twice the height of the numeral used');

INSERT INTO placement_manner_rules (description) VALUES
('Legible and prominent'),
('Retail sale price and net quantity numerals must be in a colour that contrasts conspicuously with label background (exception: blown/formed/molded glass or plastic surfaces)'),
('Hand-written/hand-scripted declarations must be clear, unambiguous, legible'),
('Language must be Hindi (Devanagari) or English (other languages may be added in addition)'),
('If package has outer wrapper/container, the wrapper must also carry all required declarations, unless wrapper is transparent and inner declarations are clearly readable through it');

-- ---- prohibited_practices ----
INSERT INTO prohibited_practices (description, exception_note) VALUES
('Affixing individual stickers to alter/make a required declaration', 'Exception: stickers may be used ONLY to reduce MRP, and must not cover the original MRP'),
('Reading a declaration through liquid commodity in the package', NULL),
('Using words like ''minimum'', ''not less than'', ''average'', ''about'', ''approximately'' in quantity declarations', NULL),
('Altering, obliterating, or smudging retail sale price once printed', NULL),
('Selling above declared MRP', NULL);

-- ---- scope_exclusions ----
INSERT INTO scope_exclusions (exclusion_type, condition_text, note) VALUES
('full_scope_exclusion', 'Packages of commodities containing quantity of more than 25 kg or 25 litre (excluding cement and fertilizer sold in bags up to 50 kg)', NULL),
('full_scope_exclusion', 'Packaged commodities meant for industrial consumers or institutional consumers', NULL),
('rule_26_full_exemption', 'net weight or measure is 10g or 10ml or less (sold by weight/measure)', 'MRP and net quantity must still be declared if package is between 10g-20g or 10ml-20ml'),
('rule_26_full_exemption', 'Fast food items packed by restaurant/hotel', NULL),
('rule_26_full_exemption', 'Scheduled and non-scheduled formulations under Drugs (Price Control) Order, 1995', NULL),
('rule_26_full_exemption', 'Agricultural farm produce in packages above 50 kg', NULL);

-- ---- e_commerce_declarations.required_fields ----
INSERT INTO ecommerce_declaration_fields (field_name) VALUES
('Name and address of manufacturer/packer/importer'),
('Country of origin'),
('Consumer care details (name, address, telephone/email)'),
('Net quantity'),
('MRP inclusive of all taxes'),
('Month and year of manufacture/import'),
('Common/generic name of the commodity');

-- ---- wholesale_package_declarations.required_fields ----
INSERT INTO wholesale_declaration_fields (field_name) VALUES
('Name and address of manufacturer/importer/packer'),
('Identity of the commodity'),
('Total number of retail packages contained, OR net quantity in standard units');

-- ---- penalties ----
INSERT INTO penalties (rule_ref, violation_category, fine_amount_inr) VALUES
('Rule 32', 'Registration violations (Rules 27 to 31)', 4000),
('Rule 32', 'Other rule violations (no specified penalty)', 2000);
