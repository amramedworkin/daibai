Here is a professional, comprehensive documentation component designed directly for your README.md. It categorizes the security boundaries based on the latest academic research on Text-to-SQL vulnerabilities, clearly communicating to users and developers exactly what the system will and will not allow.

## ---

**🛡️ Security & SQL Guardrails (Safe-by-Design Architecture)**

Because LLMs are inherently probabilistic and susceptible to Prompt Injection (GenSQLi) and hallucinations, this agent operates on an **"Untrusted Client"** model. We implement a strict, multi-layered Defense-in-Depth pipeline (Pre-LLM Sanitization \+ Post-LLM AST Validation) to protect your databases.

Below is the explicit classification of how the system handles different types of requests.

### **🛑 1\. Rejected Operations (High-Risk & Vulnerability Prevention)**

These operations pose an immediate threat to data integrity, system security, or network mapping. They are **strictly blocked** at the AST (Abstract Syntax Tree) parsing level. If the LLM generates any of these, the query is immediately killed and a SecurityViolation is raised.

* **Data Modification & Destruction (DML/DDL):** The agent is strictly read-only. Keywords such as DROP, DELETE, UPDATE, INSERT, ALTER, TRUNCATE, GRANT, and REVOKE are hard-blocked.  
* **Piggyback Attacks (Multi-Statement Injection):** Queries attempting to chain commands using semicolons (e.g., SELECT \* FROM sales; DROP TABLE users;) are blocked to prevent execution of hidden destructive payloads.  
* **System Schema Probing:** Attempts to map the backend architecture by querying information\_schema, pg\_catalog, sqlite\_master, or other system-level metadata tables are rejected.  
* **Information Disclosure (Native Functions):** Malicious attempts to steal server IPs, database names, or version numbers using functions like user(), database(), version(), or session\_user are intercepted.

### **⚠️ 2\. Dangerous Operations (Restricted & Resource Intensive)**

These requests are not necessarily destructive but can easily crash database servers or exhaust connection pools (Denial of Service). They are either blocked or automatically mitigated by the engine.

* **Time-Based & Computational DoS Attacks:** Functions specifically designed to tie up server resources (e.g., benchmark(), pg\_sleep(), waitfor) are universally blocked.  
* **Unbounded Cartesian Products:** Queries attempting to join massive tables without ON clauses or lacking a LIMIT/TOP boundary. Currently, the guardrails will flag queries attempting to return unbounded rows, requiring users to explicitly define constraints (e.g., "Show me the *top 100* users...").  
* **Out-of-Scope Schema Access (Now Permissive):** Previously a hard block, the allowed_tables check now acts primarily as a performance optimization for context window pruning rather than a strict security barrier. If a user asks for a table that was pruned from the semantic context, the system will attempt to execute it (or run a multi-pass retrieval) rather than blocking the user. Strict scope isolation can still be enabled via the `strict_scope` flag for restricted environments.

### **🔍 3\. Questionable Operations (Increased Scrutiny)**

These operations sit in a gray area. They might be the result of a poorly phrased user question, an LLM hallucination, or a sophisticated prompt injection attempt. They undergo rigorous pre- and post-processing scrutiny.

* **In-Band Prompt Injections:** If a user includes explicit SQL payloads in their natural language question (e.g., *"Which client's name is \\g DROP database?"*), the Pre-LLM Sanitizer will flag the prompt as suspicious and reject it before it ever costs an API token.  
* **Tautologies & Always-True Conditions:** Queries containing patterns like OR 1=1 or WHERE status \= 'active' OR 'a'='a'. While sometimes innocent, these are classic SQL injection techniques that LLMs are known to hallucinate. They are flagged and blocked by the AST validator.  
* **Complex UNION Smuggling:** Queries attempting to use UNION to stitch an allowed table together with an unauthorized table are deeply inspected. The AST parser traverses both sides of the UNION to ensure scope compliance.

---

🚀 **Advanced Execution Modes**

### **⚡ God-Mode (Implemented)**
"God-Mode" is an elevated execution state that overrides the read-only guardrails. When an authenticated session is granted `god_mode`, the AST validator explicitly bypasses the `_BLOCKED_KEYWORDS` (DML/DDL) and prompt-injection sanitizers. This allows trusted users and system administrators to perform automated database migrations, table creation, and mass data-updates via natural language, while still retaining base-level DoS protections (blocking `sleep()`, `benchmark()`).

### **👑 God-Emperor Mode (Coming Soon)**
While God-Mode grants full Data Manipulation and Data Definition capabilities over the active databases, **God-Emperor Mode** will elevate the agent to manage the database server itself. Slated for a future release, this mode will allow authorized users to prompt DaiBai to manage Database User Accounts, Server Authorizations, Role-Based Access Controls (RBAC), and cross-database permissions securely through natural language dialogue.