package demo.orders;

import org.springframework.stereotype.Service;

@Service
public class OrderService {
    private final OrderRepository repository;

    public OrderService(OrderRepository repository) {
        this.repository = repository;
    }

    public Order find(String id) {
        return repository.findById(id)
                .orElseThrow(() -> new OrderNotFound(id));
    }
}
