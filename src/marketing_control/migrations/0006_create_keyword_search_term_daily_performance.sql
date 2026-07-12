-- Grain: one Google Ads ad-group criterion and calendar day.
CREATE TABLE keyword_daily_performance (
    customer_resource_name VARCHAR NOT NULL,
    ad_group_criterion_resource_name VARCHAR NOT NULL,
    report_grain VARCHAR NOT NULL CHECK (report_grain = 'keyword_day'),
    report_date DATE NOT NULL,
    impressions BIGINT NOT NULL,
    clicks BIGINT NOT NULL,
    cost_micros BIGINT NOT NULL,
    conversions DECIMAL(20, 6) NOT NULL,
    conversions_value DECIMAL(20, 6) NOT NULL,
    PRIMARY KEY (
        customer_resource_name,
        ad_group_criterion_resource_name,
        report_date
    )
);

-- Grain: one Google Ads search-term-view resource and calendar day.
-- Search terms hidden by Google remain NULL and retain an explicit availability status.
CREATE TABLE search_term_daily_performance (
    customer_resource_name VARCHAR NOT NULL,
    search_term_view_resource_name VARCHAR NOT NULL,
    campaign_resource_name VARCHAR NOT NULL,
    ad_group_resource_name VARCHAR NOT NULL,
    report_grain VARCHAR NOT NULL CHECK (report_grain = 'search_term_day'),
    report_date DATE NOT NULL,
    search_term VARCHAR,
    search_term_availability VARCHAR NOT NULL CHECK (
        search_term_availability IN ('available', 'privacy_limited', 'unavailable')
    ),
    impressions BIGINT NOT NULL,
    clicks BIGINT NOT NULL,
    cost_micros BIGINT NOT NULL,
    conversions DECIMAL(20, 6) NOT NULL,
    conversions_value DECIMAL(20, 6) NOT NULL,
    CHECK (
        (search_term_availability = 'available' AND search_term IS NOT NULL)
        OR (search_term_availability <> 'available' AND search_term IS NULL)
    ),
    PRIMARY KEY (
        customer_resource_name,
        search_term_view_resource_name,
        report_date
    )
);
