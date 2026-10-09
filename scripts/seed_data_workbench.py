"""Seed script to index local images and video assets into VIGIL AI Data Workbench."""

import sys
from pathlib import Path

# Ensure root in sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.database import SessionLocal, init_db
from storage.data_workbench_service import DataWorkbenchService

def main():
    print("--- Seeding VIGIL AI Human Data Operations Workbench ---")
    init_db()
    db = SessionLocal()
    try:
        service = DataWorkbenchService(db)
        
        print("1. Indexing 707 image dataset...")
        img_res = service.index_image_dataset("dataset")
        print(f"   -> Result: {img_res}")

        print("2. Indexing video assets...")
        vid_res = service.index_video_assets(["demo_video", "data/output_demo_v2"])
        print(f"   -> Result: {vid_res}")

        print("3. Seeding baseline video episodes (AI Proposals vs Human GT)...")
        ep_res = service.seed_baseline_video_episodes()
        print(f"   -> Result: {ep_res}")

        print("4. Seeding staged recording protocol checklist...")
        stg_res = service.seed_staged_recording_protocol()
        print(f"   -> Result: {stg_res}")

        print("5. Getting overall workbench summary...")
        summary = service.asset_repo.get_summary()
        print(f"   -> Total Images: {summary.get('total_images')}")
        print(f"   -> Total Videos: {summary.get('total_videos')}")
        print(f"   -> Total Episodes: {summary.get('total_episodes')}")
        print(f"   -> Splits: {summary.get('splits')}")
    finally:
        db.close()

    print("--- Seeding Completed Successfully! ---")

if __name__ == "__main__":
    main()
