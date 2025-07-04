USE harmonaize;

/*
    Note: This is compatible with PostgreSQL and will be converted to align with
          the current models
*/

/*CORE*/
CREATE TABLE core_patient (
    id INT AUTO_INCREMENT PRIMARY KEY,
    unique_id VARCHAR(100) UNIQUE NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE core_location (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(200),
    latitude DOUBLE,
    longitude DOUBLE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CHECK (latitude >= -90 AND latitude <= 90),
    CHECK (longitude >= -180 AND longitude <= 180)
);

CREATE TABLE core_timedimension (
    id INT AUTO_INCREMENT PRIMARY KEY,
    timestamp DATETIME,
    start_date DATETIME,
    end_date DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE core_attribute (
    id INT AUTO_INCREMENT PRIMARY KEY,
    variable_name VARCHAR(200) NOT NULL,
    display_name VARCHAR(200),
    description TEXT,
    unit VARCHAR(50),
    ontology_code VARCHAR(100),
    variable_type VARCHAR(50),
    category VARCHAR(100),
    source_type VARCHAR(10) DEFAULT 'source',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE(variable_name, source_type)
);

CREATE TABLE core_observation (
    id INT AUTO_INCREMENT PRIMARY KEY,
    patient_id INT,
    location_id INT,
    attribute_id INT NOT NULL,
    time_id INT,
    float_value DOUBLE,
    int_value INT,
    text_value TEXT,
    boolean_value BOOLEAN,
    datetime_value DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE (patient_id, location_id, attribute_id, time_id),
    FOREIGN KEY (patient_id) REFERENCES core_patient(id) ON DELETE CASCADE,
    FOREIGN KEY (location_id) REFERENCES core_location(id) ON DELETE CASCADE,
    FOREIGN KEY (attribute_id) REFERENCES core_attribute(id) ON DELETE CASCADE,
    FOREIGN KEY (time_id) REFERENCES core_timedimension(id) ON DELETE SET NULL
);

/*CLIMATE*/
CREATE TABLE climate_climatemodel (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    description TEXT,
    source_url TEXT
);

CREATE TABLE climate_climateindex (
    id INT AUTO_INCREMENT PRIMARY KEY,
    observation_id INT UNIQUE,
    model_id INT,
    index_type VARCHAR(100) NOT NULL,
    units VARCHAR(50),
    notes TEXT,
    FOREIGN KEY (observation_id) REFERENCES core_observation(id) ON DELETE CASCADE,
    FOREIGN KEY (model_id) REFERENCES climate_climatemodel(id) ON DELETE SET NULL
);

CREATE TABLE climate_climateaggregate (
    id INT AUTO_INCREMENT PRIMARY KEY,
    location_id INT,
    time_range_id INT,
    mean_temperature DOUBLE,
    total_rainfall DOUBLE,
    humidity DOUBLE,
    derived_from_id INT,
    FOREIGN KEY (location_id) REFERENCES core_location(id) ON DELETE CASCADE,
    FOREIGN KEY (time_range_id) REFERENCES core_timedimension(id) ON DELETE SET NULL,
    FOREIGN KEY (derived_from_id) REFERENCES climate_climatemodel(id) ON DELETE SET NULL
);

/*GEOLOCATION*/
CREATE TABLE geolocation_adminunit (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255),
    level INT NOT NULL,
    parent_id INT,
    FOREIGN KEY (parent_id) REFERENCES geolocation_adminunit(id) ON DELETE SET NULL
);

CREATE TABLE geolocation_facility (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255),
    location_id INT UNIQUE,
    facility_type VARCHAR(100),
    code VARCHAR(50),
    FOREIGN KEY (location_id) REFERENCES core_location(id) ON DELETE CASCADE
);

CREATE TABLE geolocation_geoboundary (
    id INT AUTO_INCREMENT PRIMARY KEY,
    admin_unit_id INT,
    geojson JSON NOT NULL,
    FOREIGN KEY (admin_unit_id) REFERENCES geolocation_adminunit(id) ON DELETE CASCADE
);

/*HEALTH*/
CREATE TABLE health_condition (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    icd10_code VARCHAR(20),
    description TEXT
);

CREATE TABLE health_harmonizedhealthrecord (
    id INT AUTO_INCREMENT PRIMARY KEY,
    patient_id INT,
    location_id INT,
    condition_id INT,
    time_id INT,
    age INT,
    gender VARCHAR(10),
    outcome VARCHAR(100),
    source_observation_id INT,
    notes TEXT,
    FOREIGN KEY (patient_id) REFERENCES core_patient(id) ON DELETE SET NULL,
    FOREIGN KEY (location_id) REFERENCES core_location(id) ON DELETE SET NULL,
    FOREIGN KEY (condition_id) REFERENCES health_condition(id) ON DELETE SET NULL,
    FOREIGN KEY (time_id) REFERENCES core_timedimension(id) ON DELETE SET NULL,
    FOREIGN KEY (source_observation_id) REFERENCES core_observation(id) ON DELETE SET NULL
);
