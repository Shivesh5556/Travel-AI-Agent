/*
  Starter destinations for the home page.

  These are NOT live quotes. Each card pre-fills the planner (destination,
  trip length, interests and a starting budget per person). The agent then
  goes and finds the real numbers - and tells you if the starting budget was
  too optimistic. Edit freely: it is just a list.
*/
export const DESTINATIONS = [
  { name: "Jaipur", emoji: "🏰", tone: "sand", tagline: "Forts, bazaars, dal baati", days: 3, perPerson: 9000, interests: ["Culture & Heritage", "Food", "Shopping"], best: "Oct–Mar" },
  { name: "Goa", emoji: "🏖️", tone: "sea", tagline: "Beaches, cafés, slow evenings", days: 4, perPerson: 12000, interests: ["Relaxation", "Food", "Nightlife"], best: "Nov–Feb" },
  { name: "Manali", emoji: "🏔️", tone: "pine", tagline: "Snow, rivers, mountain roads", days: 4, perPerson: 11000, interests: ["Nature", "Adventure", "Photography"], best: "Mar–Jun" },
  { name: "Rishikesh", emoji: "🧘", tone: "river", tagline: "Rafting, ghats, yoga", days: 3, perPerson: 7000, interests: ["Adventure", "Nature", "Relaxation"], best: "Sep–Nov" },
  { name: "Udaipur", emoji: "🛶", tone: "rose", tagline: "Lakes and palace views", days: 3, perPerson: 10000, interests: ["Culture & Heritage", "Photography", "Relaxation"], best: "Oct–Mar" },
  { name: "Varanasi", emoji: "🪔", tone: "ember", tagline: "Ghats, aarti, old lanes", days: 3, perPerson: 7000, interests: ["Culture & Heritage", "Food", "Photography"], best: "Nov–Feb" },
  { name: "Kochi", emoji: "🌴", tone: "leaf", tagline: "Backwaters and spice-trail food", days: 4, perPerson: 13000, interests: ["Food", "Nature", "Culture & Heritage"], best: "Oct–Mar" },
  { name: "Darjeeling", emoji: "🍵", tone: "mist", tagline: "Tea gardens, toy train, sunrise", days: 4, perPerson: 11000, interests: ["Nature", "Photography", "Relaxation"], best: "Mar–May" },
];

export const FAQS = [
  {
    q: "How is this different from MakeMyTrip or Goibibo?",
    a: "Those sites give you a search box and let you discover the total at checkout. Here your budget is a hard rule: the agent plans, checks the total, and re-plans (flight to train, resort to guesthouse) until it fits, showing you every round.",
  },
  {
    q: "Are the prices real?",
    a: "Flight fares come from the Duffel API when a key is set. Hotel prices and train/bus fares are estimates, because no free public API sells them for India. Every estimate is labelled on screen. Nothing is silently invented.",
  },
  {
    q: "How are my days planned?",
    a: "Places are grouped with k-means clustering so each day covers one part of the city, then ordered from your hotel with nearest-neighbour so you do not cross town twice. Both algorithms are written by hand in dayplanner.py.",
  },
  {
    q: "Can I change the itinerary?",
    a: "Yes. On the plan page you can drop a stop, move it to another day, or reorder within a day. Times and kilometres re-calculate instantly. Edits stay in your browser; they do not change the saved plan or the budget.",
  },
  {
    q: "What does the watcher do after I close the tab?",
    a: "A background agent re-checks the forecast for planned trips. If a day turns rainy and it is mostly outdoor, it tells you which days to swap. Use “Re-check this trip now” on the plan page to run it on demand.",
  },
  {
    q: "Does booking actually reserve anything?",
    a: "No. Checkout runs on Razorpay test mode only. Use the test card shown on the checkout screen. No real money moves and no real tickets are issued.",
  },
  {
    q: "Why is the display currency only approximate?",
    a: "The plan is calculated in rupees. The currency switcher uses fixed, indicative rates just to give you a feel. Edit the rates in src/currency.jsx if you want them current.",
  },
];

export const DEFAULT_ORIGIN = "Delhi";
