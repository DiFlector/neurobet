FROM mcr.microsoft.com/playwright:v1.64.1-noble

WORKDIR /opt/playwright-server
RUN npm install --global playwright@1.64.1

CMD ["playwright", "run-server", "--port", "3000", "--host", "0.0.0.0"]
