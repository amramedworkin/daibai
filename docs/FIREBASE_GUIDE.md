
```mermaid
sequenceDiagram
    autonumber
    actor User
    participant FE as DaiBai Frontend (Vanilla JS)
    participant FB as Firebase UI & SDK
    participant FBsrv as Firebase Identity Servers
    participant BE as DaiBai Backend (FastAPI)
    participant DB as Cosmos DB

    User->>FE: Types "Show top 5 advertisers" & clicks Send
    FE->>FB: Check active session (firebase.auth().currentUser)
    FB-->>FE: Null (No active user)
    FE->>User: Renders Firebase UI Login Modal
    
    Note over User, FBsrv: --- Phase 1: Firebase Authentication ---
    User->>FB: Selects provider (e.g., Google) & logs in
    FB->>FBsrv: Handles OAuth/Credential exchange
    FBsrv-->>FB: Returns cryptographically signed JWT
    FB-->>FE: Triggers signInSuccessWithAuthResult
    
    Note over FE, DB: --- Phase 2: Just-in-Time Registration & Execution ---
    FE->>FB: getIdToken()
    FB-->>FE: Returns raw JWT string
    FE->>BE: POST /api/conversations (with query payload)<br/>Header: Authorization: Bearer <JWT>
    
    BE->>BE: firebase_admin.auth.verify_id_token(JWT)<br/>Extracts UID & Email
    BE->>DB: GET User where id == UID
    DB-->>BE: 404 Not Found
    
    Note over BE, DB: The backend realizes this is a brand new user!
    BE->>DB: UPSERT (Create) User Profile<br/>{id: UID, email: email, created_at: now}
    DB-->>BE: 201 Created
    
    BE->>BE: Executes LLM/SQL Logic for "Top 5 advertisers"
    BE-->>FE: 200 OK (Returns Chat Data / SQL Results)
    FE-->>User: Displays the data table in chat window
```


| Step | Component Interaction | Description of Action | Transfer Object / Payload |
| :--- | :--- | :--- | :--- |
| **1** | **User ➔ Frontend** | The user attempts to send a chat message. | `String`: "Show me the top 5 most active advertisers" |
| **2-3** | **Frontend ➔ Firebase SDK** | The frontend intercepts the click. It asks the Firebase SDK if a user is logged in. Because the response is null, it blocks the chat request and pops open the FirebaseUI widget. | *None (Local SDK check)* |
| **4-5** | **User ➔ Firebase Servers** | The user clicks a provider (like Google) and enters their credentials. Firebase handles the secure OAuth handshake completely independently of your Python backend. | `OAuth Credentials` (Handled by Google) |
| **6** | **Firebase Servers ➔ Frontend** | Firebase confirms the identity and hands the frontend SDK a secure **JSON Web Token (JWT)**. This token contains the user's unique Firebase UID, email, and expiration time. | `AuthResult Object` (contains the JWT and basic user profile) |
| **7** | **Frontend ➔ Backend** | The frontend resumes the user's original action. It packages the chat question and attaches the Firebase JWT to the HTTP headers before sending it to FastAPI. | **Header:** `Authorization: Bearer eyJhbGci...`<br>**Body:** `{"query": "Show me the top..."}` |
| **8** | **Backend (Internal)** | FastAPI's `get_current_user` dependency intercepts the request. It uses the `firebase-admin` Python SDK to mathematically verify the JWT signature against Google's public keys. It extracts the `uid` and `email`. | **Extracted Data:**<br>`{"uid": "abc123xyz", "email": "amram@example.com"}` |
| **9** | **Backend ➔ Cosmos DB** | FastAPI asks the database if it has a profile for `uid: abc123xyz`. The database returns a 404 Not Found because this user has never spoken to DaiBai before. | **Query:** `SELECT * FROM users WHERE id='abc123xyz'` |
| **10** | **Backend ➔ Cosmos DB** | **(The Registration Step):** FastAPI realizes this is a new user. It immediately inserts a new record into the database so the user has a "folder" to save their conversation history. | **JSON Payload:**<br>`{"id": "abc123xyz", "email": "amram@example.com", "created_at": "2026-02-27T..."}` |
| **11** | **Backend (Internal)** | With the user safely registered in the database, FastAPI proceeds to generate the SQL for "top 5 active advertisers", executes it, and gets the data. | *LLM & Database specific payloads* |
| **12** | **Backend ➔ Frontend** | FastAPI returns the result of the chat to the frontend, alongside the new `conversation_id` mapped to the newly registered user. | **JSON Response:**<br>`{"conversation_id": "conv_999", "answer": "Here are the top 5...", "data": [...]}` |


