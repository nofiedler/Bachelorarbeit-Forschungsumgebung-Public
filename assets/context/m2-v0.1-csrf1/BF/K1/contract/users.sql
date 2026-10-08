-- M2-v0.1: vollständige deklarative Gerüstvorgabe, keine ausgeführte Migration.
-- Keine konkreten Studienfixtures. Tabelle im laufgebundenen MySQL-Schema.
CREATE TABLE users (
    user_id INT UNSIGNED NOT NULL,
    first_name VARCHAR(15) NOT NULL,
    last_name VARCHAR(15) NOT NULL,
    user VARCHAR(15) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    password CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    avatar VARCHAR(70) NOT NULL,
    last_login TIMESTAMP NULL DEFAULT NULL,
    failed_login INT UNSIGNED NOT NULL DEFAULT 0,
    role VARCHAR(20) NOT NULL DEFAULT 'user',
    account_enabled TINYINT UNSIGNED NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id),
    UNIQUE KEY users_user_unique (user),
    CONSTRAINT users_id_domain CHECK (user_id BETWEEN 1 AND 999999),
    CONSTRAINT users_account_enabled CHECK (account_enabled IN (0, 1))
) ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
