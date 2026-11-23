// A stand-in for the agent's service on port 8003. It records the requests the
// backend sends it, with their bodies, and GET /requests lists them.
import { createServer } from "node:http";

const requests = [];

createServer((request, response) => {
  response.setHeader("Content-Type", "application/json");
  if (request.method === "GET" && request.url === "/requests") {
    response.end(JSON.stringify(requests));
    return;
  }
  let text = "";
  request.on("data", (chunk) => {
    text += chunk;
  });
  request.on("end", () => {
    if (request.method === "POST") {
      requests.push({
        method: request.method,
        path: request.url,
        body: text ? JSON.parse(text) : null,
      });
    }
    response.end(JSON.stringify({ status: "ok" }));
  });
}).listen(8003, "127.0.0.1");
