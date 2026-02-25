# **Technical Architecture: Laya Hub (Multi-Persona Edition)**

## **1\. System Philosophy**

Laya is an **Agentic Control Plane**. It treats every professional tool as an input node and every LLM capability as an output action. The architecture is designed to scale across different business departments by using a modular "Expert Agent" routing system.

## **2\. Updated Logic Stack**

* **Ingestion (n8n):** Serves as the Universal Connector. It normalizes data from 400+ platforms into a unified "Laya Event" schema.  
* **Orchestration (LangGraph):** Implements a "Router-Worker" pattern.  
  * **The Router:** A fast model (e.g., Gemini 1.5 Flash) that identifies the domain.  
  * **The Workers:** Domain-specific sub-graphs (e.g., "The Technical Fixer," "The Sales Researcher") that have unique MCP tool access.  
* **Memory (ChromaDB):** Stores "Professional Context." It remembers that "Project X" in Jira is the same as "Campaign Y" in Salesforce.

## **3\. Persona-Based Tool Access (MCP)**

* **Engineering Module:** Access to git, docker, terminal, filesystem.  
* **Sales/CS Module:** Access to web-search, crm-api, email-history.  
* **Finance Module:** Access to sql-query (ERP), calculator, excel-reader.  
* **HR Module:** Access to calendar, directory-api, doc-generator.

## **4\. Security & Trust Layer**

* **Local Context Isolation:** Sensitive local data (e.g., payroll files) is only processed via **Local LLMs (Ollama)** to prevent cloud leakage.  
* **Differential Privacy:** Laya masks PII (Personally Identifiable Information) before sending metadata to cloud orchestrators for high-level reasoning.  
* **Execution Guardrails:** A "Human-in-the-Loop" gate exists for all WRITE operations.