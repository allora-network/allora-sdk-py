#!/usr/bin/env python3
"""
Simple test to verify the new EventAttributeCondition and subscribe_new_block_events API
"""

from src.allora_sdk.protobuf_client.events import EventAttributeCondition, EventFilter

def test_event_attribute_condition():
    """Test EventAttributeCondition functionality"""
    
    # Test basic condition
    condition1 = EventAttributeCondition("topic_id", "=", "13")
    assert condition1.to_query_condition() == "topic_id = '13'"
    
    # Test CONTAINS condition
    condition2 = EventAttributeCondition("actor_type", "CONTAINS", "INFERER")
    assert condition2.to_query_condition() == "actor_type CONTAINS 'INFERER'"
    
    # Test EXISTS condition
    condition3 = EventAttributeCondition("scores", "EXISTS", "")
    assert condition3.to_query_condition() == "scores EXISTS"
    
    print("✓ EventAttributeCondition tests passed")

def test_event_filter_construction():
    """Test automatic EventFilter construction"""
    
    conditions = [
        EventAttributeCondition("topic_id", "=", "13"),
        EventAttributeCondition("actor_type", "CONTAINS", "INFERER")
    ]
    
    # Construct EventFilter like the subscribe_new_block_events method does
    event_filter = EventFilter().event_type('NewBlockEvents')
    for condition in conditions:
        event_filter.custom(condition.to_query_condition())
    
    query = event_filter.to_query()
    expected = "tm.event='NewBlockEvents' AND topic_id = '13' AND actor_type CONTAINS 'INFERER'"
    
    assert query == expected, f"Expected: {expected}, Got: {query}"
    
    print("✓ EventFilter construction tests passed")

def test_invalid_operator():
    """Test that invalid operators are rejected"""
    
    try:
        EventAttributeCondition("topic_id", "INVALID", "13")
        assert False, "Should have raised ValueError for invalid operator"
    except ValueError as e:
        assert "Invalid operator 'INVALID'" in str(e)
        print("✓ Invalid operator test passed")

if __name__ == "__main__":
    test_event_attribute_condition()
    test_event_filter_construction()  
    test_invalid_operator()
    print("\n✓ All tests passed! The implementation is working correctly.")
    
    # Show example usage
    print("\n📋 Example Usage:")
    print("```python")
    print("conditions = [")
    print("    EventAttributeCondition('topic_id', '=', '13'),")
    print("    EventAttributeCondition('actor_type', 'CONTAINS', 'INFERER')")  
    print("]")
    print("")
    print("await subscriber.subscribe_new_block_events(")
    print("    'emissions.v9.EventEMAScoresSet',")
    print("    conditions,")
    print("    my_callback")
    print(")")
    print("```")