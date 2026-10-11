# The ship image: deliver an audit artifact to the bucket, then prove it arrived.
#
# Cloud-only on purpose. The shared audit tool stays free of any cloud dependency, because the
# office appliance runs that tool too and may have no bucket to talk to. This image is the courier,
# and a build with no offsite destination simply never uses it.
#
# The base is digest-pinned with the version recorded beside the pin, the same rule as every other
# image here. An earlier stack pinned a gateway by digest with no version written down and nobody
# could answer the first question a security review asks.
#
# The version goes on its own line rather than trailing the FROM: Docker does not accept a comment
# after an instruction, and a trailing comment turns into extra arguments.
#
# AWS CLI 2.37.12, pinned 2026-10-11
FROM amazon/aws-cli@sha256:ce44b800602264c12298bbf3399055a4cefdb9ed4a098abe564afe26e7071bce

COPY deploy/cloud/ship/ship.sh /usr/local/bin/ship.sh
RUN chmod 0755 /usr/local/bin/ship.sh

# The base image's entrypoint is the aws binary itself. This image has one job.
ENTRYPOINT ["/usr/local/bin/ship.sh"]
