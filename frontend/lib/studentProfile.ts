export type StoredProfile = {
    student_id?: string;
    level?: 'beginner' | 'intermediate' | 'advanced' | string;
    goals?: string;
    topics?: string[];
    weaknesses?: string[];
    last_topic?: string;
};

const PROFILE_STORAGE_KEY = 'studentio_profile';

export function getStoredProfile(): StoredProfile | null {
    if (typeof window === 'undefined') {
        return null;
    }

    try {
        const raw = localStorage.getItem(PROFILE_STORAGE_KEY);
        if (!raw) {
            return null;
        }
        return JSON.parse(raw) as StoredProfile;
    } catch {
        return null;
    }
}

export function saveStoredProfile(profile: StoredProfile): void {
    if (typeof window === 'undefined') {
        return;
    }

    localStorage.setItem(PROFILE_STORAGE_KEY, JSON.stringify(profile));
}
