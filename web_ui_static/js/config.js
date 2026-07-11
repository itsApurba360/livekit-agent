// Call scenario profiles used to prefill identity fields and metadata sent to /api/token
const PROFILES = {
    support: {
        phone: "9062371141",
        name: "Lokesh Associates",
        agent: "Kavya (Support)",
        entity: "Customer DB"
    },
    sales: {
        phone: "9876543210",
        name: "John Doe",
        agent: "Nandini (Sales)",
        entity: "Lead DB"
    },
    outbound: {
        phone: "9876543210",
        name: "Mock Lead",
        agent: "Nandini (Outbound)",
        entity: "Mock call metadata"
    },
    custom: {
        phone: "",
        name: "",
        agent: "Auto-detect",
        entity: "Frappe Lookup"
    }
};
