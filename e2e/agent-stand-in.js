// A stand-in for the agent's service on port 8003. It records the requests the
// backend sends it, and GET /requests lists them.
import { createServer } from "node:http";

const requests = [];

createServer((request, response) => {
  response.setHeader("Content-Type", "application/json");
  if (request.method === "GET" && request.url === "/requests") {
    response.end(JSON.stringify(requests));
    return;
  }
  if (request.method === "POST") {
    requests.push({ method: request.method, path: request.url });
  }
  response.end(JSON.stringify({ status: "ok" }));
}).listen(8003, "127.0.0.1");
