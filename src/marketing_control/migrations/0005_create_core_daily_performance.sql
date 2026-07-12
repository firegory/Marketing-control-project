CREATE TABLE campaign_daily_performance (
    customer_resource_name VARCHAR NOT NULL,
    campaign_resource_name VARCHAR NOT NULL,
    report_grain VARCHAR NOT NULL CHECK (report_grain = 'campaign_day'),
    report_date DATE NOT NULL,
    impressions BIGINT NOT NULL,
    clicks BIGINT NOT NULL,
    cost_micros BIGINT NOT NULL,
    conversions DECIMAL(20, 6) NOT NULL,
    conversions_value DECIMAL(20, 6) NOT NULL,
    PRIMARY KEY (customer_resource_name, campaign_resource_name, report_date)
);

CREATE TABLE ad_group_daily_performance (
    customer_resource_name VARCHAR NOT NULL,
    ad_group_resource_name VARCHAR NOT NULL,
    report_grain VARCHAR NOT NULL CHECK (report_grain = 'ad_group_day'),
    report_date DATE NOT NULL,
    impressions BIGINT NOT NULL,
    clicks BIGINT NOT NULL,
    cost_micros BIGINT NOT NULL,
    conversions DECIMAL(20, 6) NOT NULL,
    conversions_value DECIMAL(20, 6) NOT NULL,
    PRIMARY KEY (customer_resource_name, ad_group_resource_name, report_date)
);

CREATE TABLE ad_daily_performance (
    customer_resource_name VARCHAR NOT NULL,
    ad_group_ad_resource_name VARCHAR NOT NULL,
    report_grain VARCHAR NOT NULL CHECK (report_grain = 'ad_day'),
    report_date DATE NOT NULL,
    impressions BIGINT NOT NULL,
    clicks BIGINT NOT NULL,
    cost_micros BIGINT NOT NULL,
    conversions DECIMAL(20, 6) NOT NULL,
    conversions_value DECIMAL(20, 6) NOT NULL,
    PRIMARY KEY (customer_resource_name, ad_group_ad_resource_name, report_date)
);
