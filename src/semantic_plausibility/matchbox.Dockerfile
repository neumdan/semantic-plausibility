FROM europe-west6-docker.pkg.dev/ahdis-ch/ahdis/matchbox:v4.1.21
COPY --chown=matchbox:matchbox application.yaml /config/application.yaml
COPY --chown=matchbox:matchbox build-application.yaml /config/build-application.yaml
COPY --chown=matchbox:matchbox matchbox-schema.sql /config/matchbox-schema.sql
# Install the selected IGs and dependencies during the connected build.
# Validation itself runs on an internal network with no internet access.
RUN java -Xmx1200m -Dfhir.settings.path=/config/fhir-settings.json \
    -Dspring.config.additional-location=optional:file:/config/build-application.yaml \
    -jar /matchbox.jar --hapi.fhir.only_install_packages=true
COPY --chown=matchbox:matchbox fhir-settings.json /config/fhir-settings.json
