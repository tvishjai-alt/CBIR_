import faiss
import numpy as np
import pandas as pd
import torch
import torchvision.transforms as transforms
import torchvision.models as models
from PIL import Image

class TeleRadiologyCBIR:
    """
    Member 1's AI/CBIR Engine with Context-Aware Re-Ranking.
    Ready for Member 3 to import into the Tele-Radiology Dashboard backend.
    """
    def __init__(self, index_path="faiss_database.index", db_csv="database_pool_active.csv"):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.index = faiss.read_index(index_path)
        self.db_df = pd.read_csv(db_csv)
        
        # Load DenseNet-121 Feature Extractor Backbone
        weights = models.DenseNet121_Weights.DEFAULT
        self.model = models.densenet121(weights=weights)
        self.model.classifier = torch.nn.Identity()
        self.model = self.model.to(self.device)
        self.model.eval()

        self.transform = transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
        ])

    def retrieve_similar_cases(self, image_path, patient_age, patient_gender, view_position, alpha=0.85, top_k=5):
        # 1. Feature extraction on query image uploaded by remote clinic
        img = Image.open(image_path).convert('RGB')
        tensor = self.transform(img).unsqueeze(0).to(self.device)
        
        with torch.no_grad():
            query_vec = self.model(tensor)
            query_vec = torch.nn.functional.normalize(query_vec, p=2, dim=1).cpu().numpy().astype('float32')

        # 2. Visual Similarity Search (FAISS Top-20 candidate pool)
        visual_scores, visual_indices = self.index.search(query_vec, 20)
        candidates = self.db_df.iloc[visual_indices[0]].copy()
        candidates['S_visual'] = visual_scores[0]

        # 3. Contextual Similarity Calculation
        age_norm = min(patient_age, 100) / 100.0
        gender_enc = 1 if patient_gender.upper() == 'M' else 0
        view_enc = 1 if view_position.upper() == 'PA' else 0

        age_diff = np.abs(candidates['Age_Norm'].values - age_norm)
        s_age = 1.0 - age_diff
        s_gender = (candidates['Gender_Enc'].values == gender_enc).astype(float)
        s_view = (candidates['View_Enc'].values == view_enc).astype(float)

        candidates['S_context'] = (0.5 * s_age) + (0.25 * s_gender) + (0.25 * s_view)

        # 4. Composite Score Fusion
        candidates['S_final'] = (alpha * candidates['S_visual']) + ((1 - alpha) * candidates['S_context'])

        # 5. Top-K Final Output Selection
        final_top = candidates.sort_values(by='S_final', ascending=False).head(top_k)

        # Return structured list of dicts for backend UI rendering
        results = []
        for _, row in final_top.iterrows():
            results.append({
                "image_index": row['Image Index'],
                "patient_age": int(row['Patient Age Clean']),
                "patient_gender": row['Patient Gender'],
                "view_position": row['View Position'],
                "pathologies": row['Finding Labels'],
                "visual_score": round(float(row['S_visual']), 4),
                "context_score": round(float(row['S_context']), 4),
                "final_relevance_score": round(float(row['S_final']), 4)
            })
            
        return results

# --- HOW MEMBER 3 USES THIS IN THEIR UI BACKEND ---
if __name__ == "__main__":
    cbir_engine = TeleRadiologyCBIR()
    
    # Simulating a call from Member 3's Flask/FastAPI server:
    retrieved_cases = cbir_engine.retrieve_similar_cases(
        image_path="images-224/images-224/00000007_000.png",
        patient_age=82,
        patient_gender="M",
        view_position="PA",
        alpha=0.85,
        top_k=5
    )
    
    print("\n✅ API Output for Member 3's Radiologist Dashboard:")
    import json
    print(json.dumps(retrieved_cases, indent=2))