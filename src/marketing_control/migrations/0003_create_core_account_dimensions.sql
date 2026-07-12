CREATE TABLE customers (
    customer_resource_name VARCHAR PRIMARY KEY,
    customer_id BIGINT NOT NULL UNIQUE,
    descriptive_name VARCHAR NOT NULL,
    currency_code VARCHAR NOT NULL,
    time_zone VARCHAR NOT NULL
);

CREATE TABLE campaign_budgets (
    campaign_budget_resource_name VARCHAR PRIMARY KEY,
    campaign_budget_id BIGINT NOT NULL,
    customer_resource_name VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    amount_micros BIGINT,
    explicitly_shared BOOLEAN NOT NULL,
    UNIQUE (customer_resource_name, campaign_budget_id)
);

CREATE TABLE campaigns (
    campaign_resource_name VARCHAR PRIMARY KEY,
    campaign_id BIGINT NOT NULL,
    customer_resource_name VARCHAR NOT NULL,
    campaign_budget_resource_name VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    UNIQUE (customer_resource_name, campaign_id)
);

CREATE TABLE ad_groups (
    ad_group_resource_name VARCHAR PRIMARY KEY,
    ad_group_id BIGINT NOT NULL,
    customer_resource_name VARCHAR NOT NULL,
    campaign_resource_name VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    UNIQUE (customer_resource_name, ad_group_id)
);

CREATE TABLE ad_dimensions (
    ad_group_ad_resource_name VARCHAR PRIMARY KEY,
    ad_id BIGINT NOT NULL,
    customer_resource_name VARCHAR NOT NULL,
    ad_group_resource_name VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    ad_type VARCHAR NOT NULL,
    name VARCHAR,
    UNIQUE (customer_resource_name, ad_id, ad_group_resource_name)
);
