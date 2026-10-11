import pandas as pd
import pickle
from sklearn.model_selection import train_test_split
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

# Load Hostel Dataset
df = pd.read_csv("hostel_complaints.csv")

X = df["complaint"]
y = df["new_label"]

# TF-IDF Vectorizer
vectorizer = TfidfVectorizer(stop_words="english")
X_vec = vectorizer.fit_transform(X)

# Train-Test Split
X_train, X_test, y_train, y_test = train_test_split(
    X_vec, y, test_size=0.2, random_state=42
)

# Train Model
model = LogisticRegression(max_iter=1000)
model.fit(X_train, y_train)

# Save Model & Vectorizer
pickle.dump(model, open("hostel_model.pkl", "wb"))
pickle.dump(vectorizer, open("hostel_vectorizer.pkl", "wb"))

print("Hostel Model Trained Successfully [SUCCESS]")
