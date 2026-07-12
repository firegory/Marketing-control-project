CREATE TABLE asset_attachment_daily_performance (
    customer_resource_name VARCHAR NOT NULL,
    asset_resource_name VARCHAR NOT NULL,
    asset_attachment_resource_name VARCHAR NOT NULL,
    attachment_scope VARCHAR NOT NULL CHECK (attachment_scope IN ('campaign', 'ad_group')),
    attachment_type VARCHAR NOT NULL,
    parent_resource_name VARCHAR NOT NULL,
    report_grain VARCHAR NOT NULL CHECK (report_grain = 'asset_attachment_day'),
    report_date DATE NOT NULL,
    impressions BIGINT NOT NULL,
    clicks BIGINT NOT NULL,
    cost_micros BIGINT NOT NULL,
    conversions DECIMAL(20, 6) NOT NULL,
    conversions_value DECIMAL(20, 6) NOT NULL,
    PRIMARY KEY (
        customer_resource_name,
        asset_resource_name,
        asset_attachment_resource_name,
        attachment_scope,
        attachment_type,
        parent_resource_name,
        report_date
    )
);
