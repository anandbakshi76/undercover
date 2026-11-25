// A stand-in for OpenRouter's Decisions endpoint on port 8005. It answers every
// question the same way, so the end-to-end tests grade claims without Jev:
// a grade of 2.1 (most likely a B), the same grounds, and a different section.
import { createServer } from "node:http";

const answer = {
  answers: {
    grade: {
      type: "score",
      score: 2.1,
      probabilities: { 0: 0, 1: 0.1, 2: 0.7, 3: 0.2 },
    },
    same_grounds: { type: "noul", noul: 0.9 },
    same_section: { type: "noul", noul: 0.1 },
  },
  model: "typesafe/jev-1.13",
  usage: { input_tokens: 10, output_tokens: 2, cost: 0.0004 },
};

createServer((request, response) => {
  request.resume();
  request.on("end", () => {
    response.setHeader("Content-Type", "application/json");
    if (request.method === "GET" && request.url === "/health") {
      response.end(JSON.stringify({ status: "ok" }));
      return;
    }
    if (
      request.method === "POST" &&
      request.url?.startsWith("/api/alpha/decisions")
    ) {
      response.end(JSON.stringify(answer));
      return;
    }
    response.statusCode = 404;
    response.end(JSON.stringify({ error: { message: "Not found" } }));
  });
}).listen(8005, "127.0.0.1");
