# Outbound Data Collection

This context defines the language used for customer data-collection campaigns run through outbound calls.

## Language

**Campaign Type**:
A stable category that selects a campaign-specific conversation workflow. The initial supported types are GST and Income Tax.
_Avoid_: Agent type, purpose, prompt

**GST Campaign**:
A Campaign Type for collecting GST filing information or documents from customers.

**ITR Document Collection Campaign**:
A Campaign Type that follows up on the pre-call checklist for Income Tax Return filing and records the customer's document-submission commitment or need for help.
_Avoid_: Generic income-tax call

**Pre-call WhatsApp Checklist**:
The campaign-specific list of required information and documents sent to the customer's registered WhatsApp number before the outbound call.
_Avoid_: AI-sent checklist, call-time checklist

**ITR Collection Outcome**:
The structured result of an ITR Document Collection Campaign call: checklist receipt status, promised submission date, Delivery Mode, issue or help required, and any confirmed callback time.

**Delivery Mode**:
The channel the customer commits to use for submitting documents: WhatsApp, email, or office visit.
_Avoid_: Contact method

**Promise Follow-up**:
An AI call scheduled for 11:00 AM IST on the next working day after the customer's promised submission date and made only while the campaign still shows the required documents as pending.
_Avoid_: Unconditional reminder call
