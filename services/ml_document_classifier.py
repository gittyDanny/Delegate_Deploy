from functools import lru_cache

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline


TRAINING_EXAMPLES = [
    # Utility Bill
    ("Rechnung Rechnungsdatum Gesamtbetrag Stromverbrauch Energie Anbieter Kunde Verbrauchsrechnung Vattenfall", "utility_bill"),
    ("Invoice date total amount electricity gas water utility provider customer billing address", "utility_bill"),
    ("Vattenfall Energie Rechnung Strom Gesamtbetrag Rechnungsnummer Kunde Verbrauch", "utility_bill"),
    ("E.ON Stromrechnung Verbrauchsabrechnung Zählernummer Rechnungsbetrag Rechnungsdatum", "utility_bill"),
    ("Berliner Wasserbetriebe Rechnung Wasserverbrauch Kundennummer Verbrauchsstelle Rechnungsbetrag", "utility_bill"),
    ("Utility bill provider service address invoice number due amount electricity usage", "utility_bill"),
    ("Gas bill water bill power bill Verbrauchsstelle Gesamtbetrag Anbieter Rechnungsdatum", "utility_bill"),

    # Bank Statement
    ("Kontoauszug IBAN Kontostand Buchung Soll Haben Überweisung Bank statement balance transaction", "bank_statement"),
    ("Bank statement account number transaction balance debit credit payment reference", "bank_statement"),
    ("Sparkasse Kontoauszug Kontonummer IBAN BIC Buchungstag Valuta Saldo", "bank_statement"),
    ("Commerzbank account statement opening balance closing balance payment transfer", "bank_statement"),
    ("Deutsche Bank Kontoauszug IBAN Saldo Auszugsdatum Kontoinhaber", "bank_statement"),
    ("N26 bank statement account holder IBAN balance transactions statement period", "bank_statement"),
    ("Bank account holder statement date closing balance account transactions", "bank_statement"),

    # Passport / ID
    ("Passport surname given names nationality date of birth document number expiry date authority", "passport"),
    ("Personalausweis Ausweisnummer Geburtsdatum Staatsangehörigkeit gültig bis Name Vorname", "passport"),
    ("Identity card passport document number nationality birth place expiry authority", "passport"),
    ("Reisepass Passnummer Nachname Vorname Geburtsdatum Nationalität Ablaufdatum", "passport"),
    ("ID card full name nationality date of birth expires document no", "passport"),
    ("Passport holder document number country of issue expiration date", "passport"),
    ("Ausweisdokument Nummer gültig bis Geburtsort Staatsangehörigkeit", "passport"),

    # Commercial Register
    ("Handelsregister Amtsgericht HRB Geschäftsführer Gesellschaft mit beschränkter Haftung GmbH", "commercial_register"),
    ("Commercial register company registration number managing director legal form registered office", "commercial_register"),
    ("Registerauszug HRB Stammkapital Sitz der Gesellschaft Vertretungsberechtigter Geschäftsführer", "commercial_register"),
    ("Amtsgericht Charlottenburg Handelsregister Registergericht HRB Sitz Geschäftsführer", "commercial_register"),
    ("Company register extract legal form registered office managing directors share capital", "commercial_register"),
    ("Handelsregisterauszug Firma Sitz HRB Rechtsform Geschäftsführer Stammkapital", "commercial_register"),
    ("Register number court commercial register company address legal representative", "commercial_register"),

    # Unknown
    ("random text document without invoice data no company validation no relevant fields", "unknown"),
    ("presentation slides project description agenda meeting notes university prototype", "unknown"),
    ("email conversation appointment calendar university homework unrelated content", "unknown"),
    ("meeting minutes agenda participants discussion decisions next steps", "unknown"),
    ("lecture notes exam preparation project report unrelated to kyc documents", "unknown"),
]


KEYWORD_PROFILES = {
    "utility_bill": [
        "rechnung",
        "invoice",
        "rechnungsdatum",
        "gesamtbetrag",
        "amount due",
        "strom",
        "energie",
        "gas",
        "wasser",
        "utility",
        "verbrauch",
        "verbrauchsstelle",
        "vattenfall",
        "e.on",
        "enbw",
        "berliner wasserbetriebe",
        "provider",
    ],
    "bank_statement": [
        "kontoauszug",
        "bank statement",
        "iban",
        "bic",
        "saldo",
        "kontostand",
        "account holder",
        "opening balance",
        "closing balance",
        "transaction",
        "buchung",
        "valuta",
        "sparkasse",
        "commerzbank",
        "deutsche bank",
        "n26",
    ],
    "passport": [
        "passport",
        "personalausweis",
        "identity card",
        "ausweisnummer",
        "document number",
        "passport no",
        "date of birth",
        "geburtsdatum",
        "nationality",
        "staatsangehörigkeit",
        "expiry date",
        "gültig bis",
        "surname",
        "given names",
    ],
    "commercial_register": [
        "handelsregister",
        "commercial register",
        "registerauszug",
        "hrb",
        "amtsgericht",
        "registergericht",
        "geschäftsführer",
        "managing director",
        "legal form",
        "rechtsform",
        "registered office",
        "sitz der gesellschaft",
        "stammkapital",
        "gmbh",
    ],
    "unknown": [
        "presentation",
        "slides",
        "meeting minutes",
        "agenda",
        "homework",
        "university",
        "project notes",
        "email conversation",
        "random text",
    ],
}


@lru_cache(maxsize=1)
def get_document_classifier() -> Pipeline:
    texts = [example[0] for example in TRAINING_EXAMPLES]
    labels = [example[1] for example in TRAINING_EXAMPLES]

    model = Pipeline(
        steps=[
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), lowercase=True)),
            ("classifier", LogisticRegression(max_iter=1000, class_weight="balanced")),
        ]
    )

    model.fit(texts, labels)

    return model


def classify_document(text: str) -> dict:
    model = get_document_classifier()
    normalized_text = text.lower()

    prediction = model.predict([text])[0]
    probabilities = model.predict_proba([text])[0]
    classes = model.classes_

    ml_probabilities = {
        label: float(probability)
        for label, probability in zip(classes, probabilities)
    }

    keyword_scores = calculate_keyword_scores(normalized_text)

    combined_scores = {}
    for label in classes:
        ml_score = ml_probabilities.get(label, 0.0)
        keyword_score = keyword_scores.get(label, 0.0)

        combined_scores[label] = (ml_score * 0.55) + (keyword_score * 0.45)

    predicted_type = max(combined_scores, key=combined_scores.get)
    combined_score = combined_scores[predicted_type]

    if combined_score < 0.28:
        predicted_type = "unknown"
        combined_score = max(combined_score, keyword_scores.get("unknown", 0.0))

    confidence = round(min(98, max(30, combined_score * 100)))

    return {
        "predicted_type": predicted_type,
        "confidence": confidence,
        "ml_prediction": prediction,
        "ml_probability": round(ml_probabilities.get(predicted_type, 0.0), 3),
        "keyword_score": round(keyword_scores.get(predicted_type, 0.0), 3),
        "probabilities": {
            label: round(score, 3)
            for label, score in combined_scores.items()
        },
    }


def calculate_keyword_scores(normalized_text: str) -> dict:
    scores = {}

    for label, keywords in KEYWORD_PROFILES.items():
        hits = 0

        for keyword in keywords:
            if keyword.lower() in normalized_text:
                hits += 1

        scores[label] = min(1.0, hits / 5)

    return scores