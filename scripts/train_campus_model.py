import pandas as pd
import pickle
from sklearn.model_selection import train_test_split
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

# Load Campus Dataset
df = pd.read_csv("college_campus_complaints.csv")

X = df["complaint"]
y = df["new_label"]

# TF-IDF
vectorizer = TfidfVectorizer(stop_words="english")
X_vec = vectorizer.fit_transform(X)

# Train-Test Split
X_train, X_test, y_train, y_test = train_test_split(
    X_vec, y, test_size=0.2, random_state=42
)

# Model
model = LogisticRegression(max_iter=1000)
model.fit(X_train, y_train)

# Save Model
pickle.dump(model, open("campus_model.pkl","wb"))
pickle.dump(vectorizer, open("campus_vectorizer.pkl","wb"))

print("Campus Model Trained Successfully [SUCCESS]")
