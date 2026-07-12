-- Grain: one Google Ads campaign, device, and report date.
CREATE TABLE device_daily_performance (
    customer_resource_name VARCHAR NOT NULL,
    campaign_resource_name VARCHAR NOT NULL,
    report_grain VARCHAR NOT NULL CHECK (report_grain = 'device_day'),
    report_date DATE NOT NULL,
    device VARCHAR NOT NULL,
    impressions BIGINT NOT NULL,
    clicks BIGINT NOT NULL,
    cost_micros BIGINT NOT NULL,
    conversions DECIMAL(20, 6) NOT NULL,
    conversions_value DECIMAL(20, 6) NOT NULL,
    PRIMARY KEY (customer_resource_name, campaign_resource_name, device, report_date)
);

-- Grain: one Google Ads ad group audience criterion and report date.
CREATE TABLE audience_daily_performance (
    customer_resource_name VARCHAR NOT NULL,
    ad_group_resource_name VARCHAR NOT NULL,
    ad_group_criterion_resource_name VARCHAR NOT NULL,
    report_grain VARCHAR NOT NULL CHECK (report_grain = 'audience_day'),
    report_date DATE NOT NULL,
    impressions BIGINT NOT NULL,
    clicks BIGINT NOT NULL,
    cost_micros BIGINT NOT NULL,
    conversions DECIMAL(20, 6) NOT NULL,
    conversions_value DECIMAL(20, 6) NOT NULL,
    PRIMARY KEY (
        customer_resource_name, ad_group_criterion_resource_name, report_date
    )
);

-- Grain: one Google Ads campaign, geographic target, location semantic, and report date.
-- location_semantics remains explicit: targeting, user presence, and user interest
-- are distinct Google Ads measures and must not be aggregated into one measure.
CREATE TABLE location_daily_performance (
    customer_resource_name VARCHAR NOT NULL,
    campaign_resource_name VARCHAR NOT NULL,
    geo_target_constant_resource_name VARCHAR NOT NULL,
    report_grain VARCHAR NOT NULL CHECK (
        report_grain IN (
            'location_targeting_day', 'user_presence_day', 'user_interest_day'
        )
    ),
    location_semantics VARCHAR NOT NULL CHECK (
        location_semantics IN ('targeting', 'user_presence', 'user_interest')
    ),
    report_date DATE NOT NULL,
    impressions BIGINT NOT NULL,
    clicks BIGINT NOT NULL,
    cost_micros BIGINT NOT NULL,
    conversions DECIMAL(20, 6) NOT NULL,
    conversions_value DECIMAL(20, 6) NOT NULL,
    PRIMARY KEY (
        customer_resource_name, campaign_resource_name,
        geo_target_constant_resource_name, location_semantics, report_date
    ),
    CHECK (
        (report_grain = 'location_targeting_day' AND location_semantics = 'targeting')
        OR (report_grain = 'user_presence_day' AND location_semantics = 'user_presence')
        OR (report_grain = 'user_interest_day' AND location_semantics = 'user_interest')
    )
);
