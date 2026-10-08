ARG PHP_BASE=php:8.4.26-cli-trixie@sha256:0177c7589c0715b63e5343342a7c1f1887db759ad0b0d6ad3bb949938bba6b47
FROM composer:2.10.3@sha256:af98f42dfff7c68ba8d53c2164fd9fde1087b7d449514baa38c418b1f6bc4bac AS dependencies
WORKDIR /opt/laravel
COPY assets/laravel/composer.json assets/laravel/composer.lock ./
ENV COMPOSER_ALLOW_SUPERUSER=1 COMPOSER_PROCESS_TIMEOUT=0
RUN composer install --no-interaction --prefer-dist --no-scripts --no-plugins --no-autoloader
COPY assets/laravel ./
RUN composer dump-autoload --no-scripts --no-plugins
FROM ${PHP_BASE}
# All sources/build tools already belong to the digest-pinned upstream image.
# No apt repositories, packages, or runtime dependency installation.
RUN docker-php-ext-install pdo_mysql bcmath pcntl
COPY --from=dependencies /usr/bin/composer /usr/local/bin/composer
COPY --from=dependencies /opt/laravel /opt/laravel
WORKDIR /opt/laravel
RUN composer check-platform-reqs && php artisan --version && php -m
