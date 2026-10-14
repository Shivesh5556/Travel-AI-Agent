import { useState } from "react";
import { api, loadRazorpay, rupees } from "../api.js";

/*
  Checkout, on Razorpay TEST MODE.

  The flow, and why each step exists:

    1. we collect a name, email and phone
    2. our backend asks Razorpay to create an ORDER and gives us its id
    3. Razorpay's widget opens and collects the card details INSIDE THEIR
       OWN IFRAME - this page never sees a card number, which is the whole
       point of using a hosted widget instead of building a card form
    4. Razorpay hands back a payment id and a signature
    5. our backend recomputes that signature with our secret key and only
       issues a booking reference if it matches

  Step 5 is the important one. Without it anyone could POST a fake payment id
  to /verify and get a confirmation. The check lives in backend/payments.py.

  Test card: 4111 1111 1111 1111, any future expiry, any CVV.
  No real money can move on rzp_test_ keys.
*/
export default function Checkout({ trip, onDone }) {
  const [form, setForm] = useState({ full_name: "", email: "", phone: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [booking, setBooking] = useState(trip.plan?.booking || null);

  const total = trip.plan?.budget?.total_inr || trip.total_cost_inr;

  function set(key, value) {
    setForm({ ...form, [key]: value });
  }

  async function pay() {
    setError("");

    if (!form.full_name.trim() || !form.email.trim() || form.phone.length < 8) {
      setError("Name, email and a phone number are all needed.");
      return;
    }

    setBusy(true);
    try {
      await loadRazorpay();
      const order = await api.startCheckout(trip.id, form);

      const razorpay = new window.Razorpay({
        key: order.key_id,
        amount: order.amount_paise,
        currency: "INR",
        name: "Ghumi Ghumi",
        description: `${trip.source_city} to ${trip.destination_city}`,
        order_id: order.order_id,
        prefill: {
          name: form.full_name,
          email: form.email,
          contact: form.phone,
        },
        theme: { color: "#12182b" },
        handler: async (response) => {
          // Razorpay says it went through. We do not believe it until our
          // own server has checked the signature.
          try {
            const confirmed = await api.verifyPayment(trip.id, {
              razorpay_order_id: response.razorpay_order_id,
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_signature: response.razorpay_signature,
            });
            setBooking(confirmed);
            if (onDone) onDone();
          } catch (e) {
            setError("Payment went through but verification failed: " + e.message);
          } finally {
            setBusy(false);
          }
        },
        modal: {
          ondismiss: () => setBusy(false),
        },
      });

      razorpay.on("payment.failed", (response) => {
        setError(response.error?.description || "The payment was declined.");
        setBusy(false);
      });

      razorpay.open();
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  }

  // ---------- already booked ----------
  if (booking) {
    return (
      <div className="card">
        <h3 className="card-title">Demo payment done</h3>

        <div className="pnr">
          <span className="label">Demo reference (not a PNR)</span>
          <div className="code num">{booking.pnr}</div>
        </div>

        <table className="ledger">
          <tbody>
            <tr>
              <td>Traveller</td>
              <td>{booking.traveler?.full_name}</td>
            </tr>
            <tr>
              <td>Trip</td>
              <td>
                {trip.source_city} → {trip.destination_city}
              </td>
            </tr>
            <tr>
              <td>Dates</td>
              <td>
                {trip.start_date} to {trip.end_date}
              </td>
            </tr>
            <tr>
              <td>Payment id</td>
              <td style={{ fontSize: "12px" }}>{booking.payment_id}</td>
            </tr>
            <tr>
              <td>Paid</td>
              <td>{rupees(booking.paid_inr)}</td>
            </tr>
          </tbody>
        </table>

        <div className="notice" style={{ marginTop: "20px" }}>
          {booking.disclaimer}
        </div>
      </div>
    );
  }

  // ---------- the form ----------
  return (
    <div className="card">
      <h3 className="card-title">Demo payment</h3>
      <p className="hint" style={{ marginTop: "-8px", marginBottom: "20px" }}>
        This shows how a payment would work, in Razorpay <strong>test mode</strong>. No
        money moves and <strong>no train, flight or hotel is booked</strong> - book those
        with the providers directly. Use card <strong className="num">4111 1111 1111 1111</strong>,
        any future expiry, any CVV.
      </p>

      {error && <div className="error-box">{error}</div>}

      <div className="field">
        <label htmlFor="cn">Full name</label>
        <input
          id="cn"
          value={form.full_name}
          onChange={(e) => set("full_name", e.target.value)}
        />
      </div>

      <div className="field-row">
        <div className="field">
          <label htmlFor="ce">Email</label>
          <input
            id="ce"
            type="email"
            value={form.email}
            onChange={(e) => set("email", e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="cp">Phone</label>
          <input
            id="cp"
            value={form.phone}
            onChange={(e) => set("phone", e.target.value)}
            placeholder="9876543210"
          />
        </div>
      </div>

      <div className="verdict fits" style={{ marginBottom: "16px" }}>
        <div>
          <span className="label">Test amount (the estimated trip total)</span>
          <div className="big num">{rupees(total)}</div>
        </div>
      </div>

      <button className="btn btn-primary" onClick={pay} disabled={busy}>
        {busy ? "Opening Razorpay…" : `Run test payment of ${rupees(total)}`}
      </button>
    </div>
  );
}
