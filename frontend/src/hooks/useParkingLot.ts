import useSWR from 'swr';
import { api } from '../services/api';
import { IngestItem, Entity } from '../types';

const fetchParkingLot = (status?: string): Promise<IngestItem[]> => 
  api.parkingLot.list(status);

export function useParkingLot(status?: string) {
  const key = status ? ['parkinglot', status] : 'parkinglot';
  const { data, error, isLoading, mutate } = useSWR<IngestItem[]>(
    key,
    () => fetchParkingLot(status)
  );
  
  return {
    items: data || [],
    isLoading,
    error,
    mutate,
  };
}

export function useParkingLotCount() {
  const { items, isLoading } = useParkingLot();
  
  const pendingCount = items.filter(
    item => ['parked', 'resolution_required', 'failed'].includes(item.status)
  ).length;
  
  return {
    count: pendingCount,
    isLoading,
  };
}

export async function resolveParkingLotItem(
  ingestId: string,
  data: { entity_id?: string; create_entity?: { name: string } }
): Promise<Entity> {
  return api.parkingLot.resolve(ingestId, data);
}

export async function retryParkingLotItem(
  ingestId: string
): Promise<void> {
  await api.parkingLot.retry(ingestId);
}
