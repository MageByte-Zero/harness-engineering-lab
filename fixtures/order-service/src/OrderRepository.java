package demo.orders;

import java.util.Map;
import java.util.Optional;
import org.springframework.stereotype.Repository;

@Repository
public class OrderRepository {
    private final Map<String, Order> orders = Map.of(
            "o-101", new Order("o-101", "CREATED"));

    public Optional<Order> findById(String id) {
        return Optional.ofNullable(orders.get(id));
    }
}
