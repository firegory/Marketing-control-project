CREATE TABLE keyword_criteria (
    ad_group_criterion_resource_name VARCHAR PRIMARY KEY,
    criterion_id BIGINT NOT NULL,
    customer_resource_name VARCHAR NOT NULL,
    ad_group_resource_name VARCHAR NOT NULL,
    source_status VARCHAR NOT NULL,
    keyword_text VARCHAR NOT NULL,
    match_type VARCHAR NOT NULL,
    UNIQUE (customer_resource_name, ad_group_resource_name, criterion_id)
);

CREATE TABLE ad_group_criteria (
    ad_group_criterion_resource_name VARCHAR PRIMARY KEY,
    criterion_id BIGINT NOT NULL,
    customer_resource_name VARCHAR NOT NULL,
    ad_group_resource_name VARCHAR NOT NULL,
    source_type VARCHAR NOT NULL,
    source_status VARCHAR NOT NULL,
    UNIQUE (customer_resource_name, ad_group_resource_name, criterion_id)
);

CREATE TABLE campaign_criteria (
    campaign_criterion_resource_name VARCHAR PRIMARY KEY,
    criterion_id BIGINT NOT NULL,
    customer_resource_name VARCHAR NOT NULL,
    campaign_resource_name VARCHAR NOT NULL,
    source_type VARCHAR NOT NULL,
    source_status VARCHAR NOT NULL,
    geo_target_constant_resource_name VARCHAR,
    UNIQUE (customer_resource_name, campaign_resource_name, criterion_id)
);

CREATE TABLE assets (
    asset_resource_name VARCHAR PRIMARY KEY,
    asset_id BIGINT NOT NULL,
    customer_resource_name VARCHAR NOT NULL,
    name VARCHAR,
    source_type VARCHAR NOT NULL,
    UNIQUE (customer_resource_name, asset_id)
);

CREATE TABLE asset_attachments (
    asset_attachment_resource_name VARCHAR PRIMARY KEY,
    customer_resource_name VARCHAR NOT NULL,
    attachment_scope VARCHAR NOT NULL CHECK (
        attachment_scope IN ('customer', 'campaign', 'ad_group')
    ),
    attached_to_resource_name VARCHAR NOT NULL,
    asset_resource_name VARCHAR NOT NULL,
    field_type VARCHAR NOT NULL,
    source_status VARCHAR NOT NULL
);

CREATE TABLE geo_target_constants (
    customer_resource_name VARCHAR NOT NULL,
    geo_target_constant_resource_name VARCHAR NOT NULL,
    criterion_id BIGINT NOT NULL,
    name VARCHAR NOT NULL,
    canonical_name VARCHAR NOT NULL,
    country_code VARCHAR NOT NULL,
    target_type VARCHAR NOT NULL,
    source_status VARCHAR NOT NULL,
    PRIMARY KEY (customer_resource_name, geo_target_constant_resource_name),
    UNIQUE (customer_resource_name, criterion_id)
);
