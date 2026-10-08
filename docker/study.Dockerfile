FROM composer:2.10.3@sha256:af98f42dfff7c68ba8d53c2164fd9fde1087b7d449514baa38c418b1f6bc4bac AS dependencies
WORKDIR /opt/study
COPY assets/study/m2-v0.1/scaffold/composer.json assets/study/m2-v0.1/scaffold/composer.lock ./
ENV COMPOSER_ALLOW_SUPERUSER=1 COMPOSER_PROCESS_TIMEOUT=0
RUN composer install --no-interaction --prefer-dist --no-scripts --no-plugins --no-autoloader
COPY assets/study/m2-v0.1/scaffold ./
RUN composer dump-autoload --no-scripts --no-plugins
FROM php:8.4.26-cli-trixie@sha256:0177c7589c0715b63e5343342a7c1f1887db759ad0b0d6ad3bb949938bba6b47
# mysqli is for private T4/facade counterdesigns; M1 images remain unchanged.
RUN docker-php-ext-install pdo_mysql mysqli bcmath pcntl
COPY --from=dependencies /usr/bin/composer /usr/local/bin/composer
COPY --from=dependencies /opt/study /opt/study
WORKDIR /opt/study
RUN composer check-platform-reqs && php artisan --version && php -m && chown -R www-data:www-data storage bootstrap/cache
USER www-data
CMD ["php", "artisan", "serve", "--host=0.0.0.0", "--port=8000", "--no-reload"]
