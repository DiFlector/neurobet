FROM alpine:3.22

RUN apk add --no-cache socat
USER 65534:65534
ENTRYPOINT ["socat"]
